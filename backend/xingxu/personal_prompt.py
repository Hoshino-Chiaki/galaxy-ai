"""Personal instructions and a separate, quota-limited attachment library."""
import json

from galaxy_cli.file_scope import root_directory, child_directory
from .account_storage import atomic_json, locked, read_json
from .auth import AuthFailure
from .file_storage import FileStorage, MIB

PERMISSION = 'ai:prompt:configure'
QUOTA_BYTES = 25 * MIB


class PromptFiles:
    def __init__(self, files):
        self.files = files

    def workspace(self, user):
        root = self.files.account_root(user)
        with root_directory(root) as parent, child_directory(parent, ['prompts'], create=True):
            pass
        return root / 'prompts'

    account_root = workspace


class PersonalPrompt(FileStorage):
    def __init__(self, files, config):
        super().__init__(PromptFiles(files), config)

    @staticmethod
    def permission(user, write=False):
        if PERMISSION not in user.get('permissions', []):
            raise AuthFailure('forbidden', '当前账号没有个人提示词权限。', 403)

    def settings(self):
        return {'quota_mb': 25, 'quota_bytes': QUOTA_BYTES}

    def legacy_bytes(self, user):
        return 0

    def reserve(self, user, filename, size, **kwargs):
        try:
            return super().reserve(user, filename, size, **kwargs)
        except AuthFailure as error:
            if error.code == 'storage_quota':
                raise AuthFailure('storage_quota', '提示词附件合计最多 25 MB，包含正在上传的文件。请先删除不需要的附件。', 413) from None
            raise

    def snapshot(self, user):
        self.permission(user)
        root = self.files.workspace(user)
        with locked(root / '.prompt.lock'):
            value = read_json(root / 'prompt.json', {'prompt': ''})
        return {**self.list(user), 'prompt': value['prompt']}

    def save(self, user, prompt):
        self.permission(user, True)
        root = self.files.workspace(user)
        with locked(root / '.prompt.lock'):
            atomic_json(root / 'prompt.json', {'prompt': prompt})
        return {'prompt': prompt}

    def messages(self, user):
        if PERMISSION not in user.get('permissions', []):
            return []
        value = self.snapshot(user)
        if not value['prompt'] and not value['files']:
            return []
        attachments = [{key: item[key] for key in ('id', 'filename', 'size', 'media_type')}
                       | {'read_path': '/account-prompts/' + item['path'].removeprefix('library/')}
                       for item in value['files']]
        return [{'role': 'system', 'content':
            '【当前账号的个人提示词；本轮已重新加载】\n'
            '以下是本人的长期偏好和提示词附件。按其中的语言、格式和业务习惯服务本人；'
            '与服务器全局规则冲突时遵循全局规则，不能改变登录身份、业务权限或文件访问范围。'
            '每轮回答前，先逐个读取下面所有提示词附件；文件位于只读 /account-prompts，'
            '可用共享 Python 解析库按格式读取。不得只根据文件名猜测内容；'
            '文件无法解析或超出当前模型上下文时须如实说明，不能声称已经完整阅读。\n'
            + json.dumps({'prompt': value['prompt'], 'attachments': attachments}, ensure_ascii=False)}]
