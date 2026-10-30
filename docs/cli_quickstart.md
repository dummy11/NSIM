# nsim 常用命令简版

全部子命令、默认值、互斥关系和高级选项见 [完整命令清单](cli_reference.md)。本页使用正式简写，所有原命令和长选项继续支持。稳定启动器需使用包含简写支持的新版脚本。

## 日常只记这几个

以下示例在已配置好的验证工程目录内执行，假设已有可用的 `submit/rtl.json` 和 `caselist/smoke.list`。裸 submit 名称会从工程的 `submit/` 下查找；清单路径相对当前目录。

```sh
# 保存默认输出位置一次，后续不必反复输入 -o
mkdir -p /scratch/my_project_out
nsim up -o /scratch/my_project_out

# 看当前工程、创建并检查 submit
nsim st
nsim sub new debug
nsim sub show debug
nsim sub check debug

# 单跑：编译、仿真、检查
nsim run -s rtl -c smoke -S 42

# 分开编译与仿真
nsim com -s rtl
nsim sim -s rtl -c smoke -S 42

# 回归：4 个并发任务
nsim regr -s rtl -l caselist/smoke.list -j 4

# 每个用例使用 10 个不同随机 seed
nsim regr -s rtl -l caselist/smoke.list -r 10 -j 4

# 只生成执行计划和命令预览
nsim run -s rtl -c smoke -S 42 -n
```

输出根改成实际可写且位于源码树外的目录。`submit new debug` 生成的配置可能仍需填写工具或平台命令，先按工程环境配置再运行。原生 VCS 适配器当前只提供 dry-run；实际任务使用已接好的 command 包装器或 fake 示例。

## 六个常用选项

| 写法 | 含义 | 例子 |
| --- | --- | --- |
| `-s` | submit 名称，可多个 | `-s rtl rtl_debug` |
| `-c` | 单个逻辑用例 | `-c smoke` |
| `-l` | 用例清单，可多个 | `-l smoke.list stress.list` |
| `-S` | 固定随机种子，可多个 | `-S 42 43` |
| `-j` | 并发任务数 | `-j 8` |
| `-o` | 临时输出根 | `-o /scratch/debug_out` |

`-c` 与 `-l` 二选一；普通回归用 `-l`，批量回归列表用 `-rl`。`-r` 仅用于 regr，与 `-S` 二选一。不指定 seed 时随机生成一个并记录。

`-j` 在执行和重跑命令中均可使用。`-s` 和 `-S` 区分大小写。

## 按需添加

| 想做的事 | 添加的参数 |
| --- | --- |
| 从工程外运行 | `-p 工程名或路径` |
| 单独开一个调试工作区 | `-d debug_123` |
| 指定编译目录 | `-cd build` |
| 指定仿真目录 | `-sd results` |
| 本机执行 | `-sched local` |
| 使用 LSF 队列 | `-sched lsf -q QUEUE` |
| 只预览 | `-n` |

进入工程后省略 `-p`；使用 submit 的默认模式时省略 `-mode`；默认工作区也是该模式。队列、资源、license 等重复设置放进公共 submit，日常只切换 `-s`。

## 报告与失败处理

```sh
# 将路径替换为运行返回的 record_dir
nsim rpt show /path/to/record_dir
nsim rr /path/to/record_dir
nsim rr /path/to/record_dir -j 4
nsim rc /path/to/record_dir -o /scratch/backup_out
nsim disk check /scratch/my_project_out
```

`sim` 要求已有匹配的编译产物。`rerun` 固定失败 case/seed，使用当前输入强制重编译，在来源回归下新建独立目录；不支持 seed 或输出根覆盖。`recover` 使用历史输入和版本恢复未完整执行的任务。两者均支持 `-dir` 指定新目录名。详见 [重跑、恢复与报告](replay_reports.md)。

## 初始化与版本管理

```sh
# 新工程注册：路径换成实际工程路径，版本需已安装
nsim init \
  -vr /work/chip/verification \
  -rtl /work/chip/rtl \
  -rf /work/chip/rtl/rtl.f \
  -o /scratch/my_project_out

nsim ls
nsim st -p verification
nsim use 0.1.0 -p verification
nsim ver
```

以下三种管理命令需要稳定启动器；包内 CLI 不提供它们。首次安装见 [使用指南](../user_guide.md#第一次使用先取得安装器)。

```sh
nsim vers
nsim inst -b ./nsim-offline-0.1.0.tar.gz
nsim pack create -w ./nsim-0.1.0-py3-none-any.whl \
  -wh ./wheelhouse -o ./nsim-offline-0.1.0.tar.gz
```

`nsim update` 改工程配置，`nsim inst` 装工具版本。`nsim init` 创建验证工程；`python3 nsim-install.py bootstrap` 创建启动器。

## 命令和长选项对照

| 简写 | 原写法 |
| --- | --- |
| `init / ls / st / up / use` | `bootstrap / list / status / update / switch` |
| `sub / proj` | `submit / project` |
| `rr / rc / rpt / disk` | `rerun / recover / report / storage` |
| `ver / caps / vers` | `version / capabilities / versions` |
| `inst / pack` | `install / bundle` |
| `-p / -c / -s / -S` | `--project / --case / --submit / --seed` |
| `-m / -d / -cd / -sd` | `-mode / -dir / -com-dir / -sim-dir` |
| `-vr / -rtl / -rf / -vf / -nv` | `--verification-root / --rtl-root / --rtl-filelist / --verification-filelist / --nsim-version` |
| `-n / -sched / -R / -lic` | `--dry-run / --scheduler / --resource / --license` |
| `-retry / -rt / -fb / -fi` | `--retries / --retry-seconds / --min-free-bytes / --min-free-inodes`；前两项只接受 0，不再自动重试或切根 |

## 长命令变短的例子

显式写法：

```sh
nsim regr --project /work/chip/verification \
  --submit /work/chip/verification/submit/rtl.json \
  --caselist /work/chip/verification/caselist/smoke.list \
  --seed 42 --jobs 8 --output-root /scratch/my_project_out
```

进入工程并保存默认输出根后，同样的运行选择可以写成：

```sh
cd /work/chip/verification
nsim up -o /scratch/my_project_out  # 只需配置一次
nsim regr -s rtl -l caselist/smoke.list -S 42 -j 8
```

查询任何命令的详细帮助：`nsim 命令 -h`，例如 `nsim regr -h`、`nsim submit new -h`。
