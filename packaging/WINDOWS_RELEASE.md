# Windows 发布包

当前 fork 支持生成可直接双击运行的 Windows x64 应用，无需目标电脑安装 Python。

## 使用

完整解压 `CrapCleaner-<版本>-Windows-x64-onedir.zip`，双击其中的
`CrapCleaner.exe`。可以把整个程序文件夹放到 E 盘或 F 盘，并创建桌面快捷方式。
必须保留同目录的 `_internal` 运行库文件夹。

程序包中的 `使用说明.txt`、`LICENSE`、`third-party-licenses` 和
`build-info.json` 应与程序一起分发。ZIP 旁的 `.sha256` 文件用于校验完整性。
构建信息会记录源码提交、未提交文件状态、Python 和打包依赖版本。

这是一份免安装程序包，用户设置和历史记录仍沿用程序原有的用户数据目录。
升级时先退出程序，再完整替换程序文件夹。

## 本机构建

在仓库根目录使用 Python 3.12 执行以下 PowerShell 命令：

```powershell
py -3.12 -m venv build\windows-venv
.\build\windows-venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\build\windows-venv\Scripts\python.exe scripts\package_windows.py
```

产物位于 `release\`。默认生成启动较快的文件夹版本及 ZIP。
脚本会运行 EXE 的版本检查和离屏 GUI 启动检查，附带许可证、构建信息和
SHA-256 校验文件。构建期间使用隔离的 DLL 搜索路径，避免将其他软件的
同名运行库误带入发布包；启动检查使用独立测试配置，不操作实际清理或迁移。
已有同名发布包时脚本会拒绝覆盖，可使用 `--output release\another-build`
指定新目录。

需要单文件版本时：

```powershell
.\build\windows-venv\Scripts\python.exe scripts\package_windows.py --mode onefile
```

单文件 EXE 启动时会临时解压运行库；如果希望运行库保存在其他盘，优先使用默认的
文件夹版本。

## 更新与发布

检查更新与下载更新均指向 `NiteFlightxx/crapcleaner`。文件夹版本只允许整包替换，
避免仅替换 EXE 后与旧运行库混用。此本地打包命令不会上传文件或创建 GitHub Release。
现有 GitHub 工作流仍可通过版本标签构建单文件发布；发布前应使用新的版本号，
并在 fork 上检查工作流结果。

当前产物没有代码签名，不能声称为已签名安装包。
