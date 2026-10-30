# TOML 名称与文件映射

项目 TOML 是文件的“地址簿”：命令只写简称，配置决定文件在哪里。
`-sub`、`-l`、`-rl` 和 `submit show/check` 共用解析规则；`submit new` 的创建位置与默认父模板也从 TOML 读取。
目录可以任意命名，不要求叫 submit、caselist 或 regression。bootstrap 只把这些名称作为初始配置写入。

## 最直接的方式：一个名字对应一个文件

在项目 `.nsim/nsim.toml` 中保留原有字段，追加：

```toml
[catalog.submit.aliases]
rtl = "flows/vcs/base.json"
debug = "flows/debug/trace.json"

[catalog.caselist.aliases]
smoke = "tests/quick.list"
conv = "tests/npu/conv.caselist"

[catalog.regrlist.aliases]
nightly = "plans/nightly.list"
```

路径相对验证工程根目录（`.nsim/` 的父目录），不是相对 `.nsim/` 或终端当前目录。
schema 1 使用配置后的 `source_root`。允许工程外路径和绝对路径；不执行环境变量、shell 或 case/seed 展开。

```sh
nsim run -sub rtl --case conv_basic
nsim regr -sub rtl -l smoke
nsim regr -rl nightly
```

工程外执行时加 `--project 工程名或路径`。更换文件位置只需修改映射，命令中的名称可以保持不变。
别名可以引用任意文件名和扩展名；实际内容仍按对应 submit、caselist 或 regression list 格式校验。
别名不改写 submit JSON 内部的 `name`，输出目录仍使用该内部名称。

## 文件较多时：配置目录，按文件名查找

不必登记每一个文件，可以按类型配置命名目录：

```toml
[catalog.submit.roots]
local = "flows/vcs"
shared = "../shared_verification/submits"

[catalog.caselist.roots]
local = "tests/suites"

[catalog.regrlist.roots]
local = "plans/regressions"
```

`-sub rtl` 查找配置目录中的 `rtl.json`；`-l smoke` 查找 `smoke.list` 或 `smoke.caselist`；
`-rl nightly` 查找 `nightly.list` 或 `nightly.regr`。也可以保留扩展名。
不自动递归扫描。目录内的子目录可用 `local:npu/smoke` 指定，或配置别名。
`local`、`shared` 只是可自定义的命名空间，不代表优先级。

多个目录命中不同文件时直接报错，使用 `local:rtl`、`shared:rtl` 或显式别名选择。
同一目录同时存在 `smoke.list` 与 `smoke.caselist` 时也报错，可写 `local:smoke.list` 消除歧义。
多个入口指向同一真实文件时去重。`submit`、`caselist`、`regrlist` 各自查找，允许类型之间同名。
`aliases` 与 `roots` 可以并用；显式别名优先。别名目标缺失时失败，不回退到目录查找。

## 名称与路径的边界

| 写法 | 含义 |
| --- | --- |
| `rtl`、`rtl.json` | TOML 名称查找，忽略当前目录的同名文件 |
| `shared:rtl` | 指定 TOML 中的命名目录 |
| `shared:nested/rtl` | 该命名目录下的相对位置，不允许 `..` |
| `./rtl.json`、`../flows/rtl.json`、`flows/rtl.json` | 显式路径，相对命令启动目录 |
| `/work/shared/rtl.json` | 显式绝对路径 |

显式路径不需要配置 catalog；缺失时直接报错，不尝试名称查找。
批量清单的 `-sub/-l` 使用该行选定工程的 TOML；行内显式相对路径仍相对启动命令的目录，保留原来的路径语义。
`-rl nightly` 本身通过外层 `--project` 或当前工程选择映射；外层指定显式清单路径时，可以由各行分别指定工程。

## 创建 submit

```toml
[catalog.submit]
write_root = "local"

[catalog.submit.roots]
local = "flows/vcs"

[catalog.submit.templates]
rtl = "flows/common/rtl.json"
gate = "flows/common/gate.json"
```

```sh
nsim submit new debug
nsim submit new gate_debug --mode gate
nsim submit new custom --extends ../common/special.json
```

`debug.json` 写到 `write_root` 指定的目录，默认父模板来自 `templates.rtl`；模式可按需配置。
`--extends` 仍是显式路径，相对新 submit 所在目录，也接受绝对路径；生成的 JSON 保存相对父文件路径。
省略 `write_root` 时创建命令报错；不会猜目录。缺失的已配置模式模板可在工程内生成，已有文件不覆盖。
新文件及新模板的写入限于当前工程，沿用项目事务与符号链接保护；工程外的目录和别名可用于读取。

## case 不需要新增 manifest

`--case conv_basic` 表示“运行名为 conv_basic 的用例”，不表示读取 `conv_basic` 文件。
具体调用哪个 UVM test、sequence 或 C test，继续由 submit 的 `case_defaults`、caselist 行参数和 plusargs 决定。

例如，submit 可设置 `uvm_test=chip_base_test`、`seq={case}`，让多个逻辑用例使用同一个 UVM test。
回归清单中的 `conv_small seq=conv_seq +WIDTH=16` 可以表达一个参数变体，不需要新建 manifest。
现有 `case.yaml` 仍为可选配置，只由 caselist 的 `@config` 显式引用；不会自动加载，也不自动影响 CLI 单跑。
若未来需要独立复用复杂的逐用例配置，再单独设计 manifest，本次不增加这一维护负担。

## 查询、记录与旧工程迁移

```sh
nsim catalog list submit
nsim catalog list caselist
nsim catalog list regrlist
nsim catalog resolve submit rtl
nsim catalog resolve regrlist nightly
```

查询返回 JSON，包括命名空间、实际路径、别名目标和命中规则。list 只列配置目录的直接文件及显式别名，
不代表所有条目都经过内容验证；submit 内容验证仍使用 `submit check`。
运行计划保存原始引用、解析后的路径和命中规则，并保留项目配置快照；选中文件进入既有哈希校验。
不把整个目录中的其他文件作为本次运行依赖。批次另保存外层清单引用和映射来源配置快照。
执行节点使用计划中的路径，不重新按名称查找。历史 rerun/recover 仍绑定记录的来源文件，
修改别名不会把旧任务悄悄转向另一个 submit；需要新绑定时用新的 run/regr 命令。

旧工程没有 catalog 时，显式路径照常可用，短名称不再回退到固定 `submit/` 或当前目录。
需要短名称时添加 aliases 或 roots；要使用 submit new，还需 write_root 和对应 templates。
`nsim update` 保存其他设置时保留 catalog，重复 bootstrap 不改写已有工程配置。
原来表示当前目录文件的 `-l smoke.list`、`-rl nightly.list`、`-sub rtl.json`，可改写为
`./smoke.list`、`./nightly.list`、`./rtl.json`，或加入映射。

RTL/TB filelist 已由项目字段显式配置；submit.files 的资源路径、hooks 的脚本路径及 extends 的路径基准保持原有规则。
catalog 负责选择配置入口，不自动扫描源码、脚本、资源，也不改变运行产物目录规则。
