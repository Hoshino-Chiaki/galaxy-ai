# 固定登录依赖源码

这里仅保留生产登录程序实际使用的源码，不包含研究抓包和浏览器数据。

- `slider-captcha-lab/human_track.py`：轨迹生成器，固定提交 `3424e33a045cc66a5b6725aea43da41aca047930`，附原 MIT 许可证。
- `youzan-slider/utils.py`：现有有赞图片定位与加密封装。

这两个文件由 `session_research/youzan-login-runtime.json` 校验 SHA-256，修改时需要同步评估原登录流程和测试。Python 依赖记录在 `session_research/requirements-login.lock.txt`，运行环境在服务器单独准备。
