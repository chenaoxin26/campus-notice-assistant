# QQ 实际测试流程

用专门测试群检查自己的电脑有没有漏采。消息由本人从手机发送，程序不会代发。下面的表格用于填写结果，不代表已经测试通过。

## 先做一次晚间补采测试

1. 在助手中添加三个测试群的完整名称，并让它们出现在 QQ 会话列表里。停止其他采集版本，避免多个程序同时切换窗口。
2. 给这次测试取一个编号，如 `CAMPUS-日期-序号`。每群从手机发 10 条带不同编号的消息，记录哪些确实发成功了。可另加普通闲聊、没有日期的任务、改期通知，以及两个人发送的相同文字。
3. 在助手中开始通知监听后离开电脑。电脑保持开机、联网且不睡眠。
4. 回来后，先核对补采前保存了哪些编号，再点击“晚间补采并整理”，等每个群处理完成。
5. 搜索消息原文中的编号，填写下表。手动粘贴补充的内容另记，不能算作自动读到。

|群名|确认发送数量|补采前已保存|补采后已保存|仍缺少|原因或待核对|
|---|---:|---:|---:|---:|---|
|测试群一||||||
|测试群二||||||
|测试群三||||||

按同一个群里的完整消息逐条核对。通知卡片数、界面行数和任务数可能都不等于真实消息数，不能直接替代。只有摘要、读取失败或未加载的内容要单独记录，原因不清楚就写“待核对”。

消息超过窗口范围时仍可能漏读。手动翻页后读到的内容，要注明人工操作，不能写成程序自动读取了全部历史。

## 需要更细的记录时

仓库保留了编号测试工具。它将清单和结果写入 `data/collector/trials`。在项目文件夹打开终端，执行：

```bat
.venv\Scripts\python.exe collector\trial.py prepare --group 测试群一 --group 测试群二 --group 测试群三 --per-phase 10
```

工具会生成 A 最小化、B 关屏两组编号。每个编号单独发成一条文字，不要合并发送。然后填写 `发送核对表.csv` 的 `send_status` 列。

|填写值|含义|
|---|---|
|`confirmed_sent`|确认已发送成功|
|`not_sent`|没有发送，或跳过该步骤|
|`failed`|发送失败|
|`planned`|还没有确认，保持原值|

测试轮读会打开群聊，可能让 QQ 显示到前台、把消息变成已读。先停止界面中的实验轮读，再运行下面的命令。它测试的是 QQ 窗口读取，不能用结果代表系统通知监听。

```bat
.venv\Scripts\python.exe collector\rotation.py --group 测试群一 --group 测试群二 --group 测试群三 --seconds 600 --trial-folder "清单目录"
```

工具记录读取时的窗口状态。发送时是否确实最小化或关屏，还需要自己确认。若加上 `--restore-minimized`，程序可能恢复窗口，不能再把整段过程算作始终最小化。

返回电脑后，单独运行晚间补采，再分别核对两次结果：

```bat
.venv\Scripts\python.exe collector\rotation.py --mode evening --idle-seconds 0 --restore-minimized --group 测试群一 --group 测试群二 --group 测试群三 --trial-folder "清单目录"
.venv\Scripts\python.exe collector\trial.py confirm --folder "清单目录" --from-csv
.venv\Scripts\python.exe collector\trial.py evaluate --folder "清单目录" --run-id "白天run_id" --label daytime
.venv\Scripts\python.exe collector\trial.py evaluate --folder "清单目录" --run-id "晚间run_id" --label evening
```

把“清单目录”换成刚生成的文件夹路径，把 `run_id` 换成对应那次运行的编号。白天轮读与晚间补采分开统计，不把结果混在一起。

不能关屏就跳过 B，标明未测试。锁屏时不绕过系统安全机制。系统通知监听的结果需要另外按手机发送清单逐条核对。
