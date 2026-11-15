"""Scoped local image upload and explicit public image URL ingestion."""
import json
import os
import urllib.error
import urllib.request
import uuid
from jsonschema import Draft202012Validator
from ...file_scope import relative_parts
from ...youzan_http import NoRedirect
from ...youzan_catalog import ID, OUTPUT, object_schema, invalid, client_context, must, success, public_image_url, image_bytes, write_lock
from ...registry import ToolError
from .material_list import run as list_images

INPUT=object_schema({'category_id':ID,'url':{'type':'string','minLength':1,'maxLength':4096},
    'path':{'type':'string','minLength':1,'maxLength':2048}},['category_id'])
INPUT['oneOf']=[{'required':['url'],'not':{'required':['path']}},{'required':['path'],'not':{'required':['url']}}]
DESCRIPTION=('将图片保存到当前店铺素材库，调用 galaxy-cli youzan material-upload --input JSON。必填 category_id；url 与 path 必须二选一。'
    'url 是可公开访问的 HTTP/HTTPS 图片内容地址，不能是普通网页、本机或私网；path 是当前账号工作目录内的相对文件路径，'
    '不接受绝对路径、目录跳转、隐藏目录、符号链接、硬链接或特殊文件，仅支持有效 JPEG/PNG/GIF/WebP 图片。'
    '用户未明确目标分组时先询问分组或是否放未分组，未分组也要查询真实 ID，不能传 0。成功返回完整实际上传响应；'
    '常见 data.attachment_id 或 attachmentId 为素材 ID，attachment_full_url/attachmentFullUrl 为有赞原图，size/attachment_size 为字节，width/height 为像素。'
    '只有上传完成且在目标分类回读确认才报告成功；申请上传凭证不等于入库，凭证不输出给模型。'
    '素材入库不会自动修改商品。分销商品可使用本店有权使用的素材；修改商品主图仍须调用 product-update，并受该商品的编辑权限限制。')


def normalize(arguments):
    if not isinstance(arguments,dict) or not Draft202012Validator(INPUT).is_valid(arguments): invalid('上传必填 category_id，url 与 path 必须且只能填写一项。')
    if 'path' in arguments: relative_parts(arguments['path'])
    return dict(arguments)


def attachment(result):
    data=result.get('data',result)
    if isinstance(data,dict) and isinstance(data.get('data'),dict):data=data['data']
    if isinstance(data,dict) and str(data.get('attachment_id',data.get('attachmentId',''))).isdecimal():return data
    raise ToolError('upload_unverified','上传请求可能已提交，但未返回素材 ID，请查询目标分类后再操作，不要盲目重复上传。',1)


def run(client,context,args):
    args=normalize(args);client.category(args['category_id'])
    with write_lock(context,'material-upload:'+args['category_id']):
        if 'url' in args:
            result=client.request('/v4/materials/api/shop/fetchPubImg.json',method='POST',form=True,body={
                'attachment_url':public_image_url(args['url']),'categoryId':args['category_id'],'channel':'wsc_web'})
            if not success(result):return result
        else:
            raw,kind,dimensions=image_bytes(context['workspace_directory'],args['path'])
            policy_result=client.request('/v4/materials/api/image/policy.json')
            policy=must(policy_result)
            if isinstance(policy,dict) and policy.get('enabled'):
                for key,value in [('maxWidth',dimensions[0]),('maxHeight',dimensions[1]),('maxPixels',dimensions[0]*dimensions[1])]:
                    limit=policy.get(key)
                    if isinstance(limit,(int,float)) and not isinstance(limit,bool) and limit>0 and value>limit:
                        raise ToolError('image_policy','图片尺寸超过当前店铺上传策略，请调整图片后重试。',1)
            token_data=must(client.request('/v4/materials/api/shop/pubImgUploadToken.json',method='POST',body={'channel':'wsc_web'}))
            token=token_data.get('uptoken') if isinstance(token_data,dict) else None
            if not isinstance(token,str) or not token or len(token)>16384 or any(c in token for c in '\r\n\x00'):
                raise ToolError('upload_authorization','有赞未返回有效上传凭证。',1)
            boundary='galaxy'+uuid.uuid4().hex
            filename='image.'+{'JPEG':'jpg','PNG':'png','GIF':'gif','WEBP':'webp'}[kind]
            mime={'JPEG':'image/jpeg','PNG':'image/png','GIF':'image/gif','WEBP':'image/webp'}[kind]
            chunks=[]
            for key,value in [('token',token),('x:categoryId',args['category_id'])]:
                chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
            chunks.extend([f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n'.encode(),raw,f'\r\n--{boundary}--\r\n'.encode()])
            request=urllib.request.Request('https://upload.qiniup.com',data=b''.join(chunks),method='POST',headers={
                'Content-Type':'multipart/form-data; boundary='+boundary,'Accept':'application/json','User-Agent':'Mozilla/5.0'})
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
            try:
                with opener.open(request,timeout=60) as response: payload=response.read(2*1024*1024+1)
                if len(payload)>2*1024*1024:raise ValueError()
                result=json.loads(payload.replace(token.encode(),b'[hidden upload credential]'))
                if not isinstance(result,dict):raise ValueError()
            except (urllib.error.URLError,OSError,TimeoutError,ValueError):
                raise ToolError('upload_unverified','上传未能确认完成，请先查询目标分类；不自动重复上传。',1) from None
            # Never return a token or authentication metadata from an upload response.
            if 'code' in result and result['code']!=0:return client.redact(result)
        row=attachment(result)
        identifier=str(row.get('attachment_id',row.get('attachmentId')))
        listing=list_images(client,{'category_id':args['category_id']})
        items=must(listing).get('items',[])
        if not any(str(item.get('attachmentId'))==identifier for item in items):
            raise ToolError('upload_unverified','上传已返回素材 ID，但目标分类尚未查到，请稍后查询，不要重复上传。',1)
        client.state.remember([row])
        return client.redact(result)


def register(registry):
    @registry.tool(name='youzan.material-upload',category='youzan',description=DESCRIPTION,input_schema=INPUT,output_schema=OUTPUT)
    def invoke(arguments):
        args=normalize(arguments);client,context=client_context('material-upload',args);return run(client,context,args)
