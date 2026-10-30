# Caselist 语法与示例

`case` 始终是 logic case name（逻辑用例名），用于目录、报告、选择与重跑。
第一列是逻辑用例名；其余列为语义字段或原始 plusargs。字段使用 `seq`，不接受 `sequence`。
示例仅展示参数组织方式；test/seq/C 程序、镜像、参数解析逻辑和检查器需要工程提供。
原生 VCS 适配器当前仅支持 dry-run；实际执行使用工程的 command 包装器。

## 文件清单

| 文件 | 展示内容 |
| --- | --- |
| `00_legacy.caselist` | 旧的一行一个名称、注释、去重 |
| `01_uvm.caselist` | 默认 UVM test、逻辑名与 test 名不同、行内覆盖 |
| `02_seq.caselist` | 共享 UVM test、默认 seq、同一 seq 的多个参数变体 |
| `03_c_test.caselist` | C test、共享 UVM test、C-only 入口 |
| `04_plusargs.caselist` | 直接 plusargs、开关、空值、引号、含空格和 # 的值 |
| `05_mixed.caselist` | 混合入口、null 清除、三种入口同时存在、无入口参数 |
| `06_mappings.caselist` | 自定义映射、新增字段、原生运行选项、直接参数覆盖映射 |
| `07_overrides.caselist` | 可选 YAML 用例覆盖以及未选择的用例不执行 |
| `case.yaml` | 自定义映射与少量用例覆盖，仅显式引用时加载 |

所有示例使用不同逻辑名，可一起选择；列表中的重复名称仅在有效参数相同时去重。

## 清单语法

```text
@defaults uvm_test = chip_base_test seq = {case}

conv_basic_seq
conv_stride1 seq=conv_stride_seq +STRIDE=1
conv_stride2 seq = conv_stride_seq +STRIDE=2
debug_case uvm_test=chip_debug_test seq=debug_seq +TRACE
```

`@defaults` 最多一次，写在用例之前；可省略，也允许空的 `@defaults` 显式选择新语法。
等号两侧允许空格。逻辑用例名是 1–128 位安全标识符，首字符为字母或数字，
其余允许字母、数字、下划线、点和连字符。空行和引号外的 # 注释被忽略。
值中有空格或 # 时使用引号，例如 `+MESSAGE="hello # world"`；不执行 shell 展开或命令替换。
plusargs 使用 `+KEY=VALUE`、`+KEY=` 或 `+FLAG`；参数名和等号之间不加空格。

默认映射：`uvm_test=X` → `+UVM_TESTNAME=X`；`seq=X` → `+SEQ_NAME=X`；
`c_test=X` → `+C_TEST=X`。后两种参数名是默认工程约定，需要 testbench 支持。
`{case}` 替换为当前逻辑用例名；字面花括号写为 `{{` 和 `}}`。
语义字段值为 `null` 表示清除继承值，包括同映射键的低优先级 plusarg；
`+KEY=null` 则是普通字符串参数，不表示删除。
缺省字段继续继承。三个入口字段不要求齐全，也不强制至少一个非空；工程负责入口有效性。
未知字段报错；需要额外字段时显式声明映射。原始 plusargs 无需登记映射。

纯名称旧清单和 CLI `--case NAME` 在 VCS 下，且 submit 未配置入口时，兼容默认
`+UVM_TESTNAME=NAME`。包含指令或行内参数的清单使用新语法，不自动补入口字段。
command/fake 的纯名称用法保持原行为。

## 可选 case.yaml

```text
@defaults uvm_test=chip_base_test seq={case}
@config ./case.yaml
custom_case seq=conv_seq image=images/boot.hex +TRACE
```

`@config` 最多一次，放在用例之前；相对路径以当前 caselist 所在目录为基准。
默认不查找、不要求 case.yaml。指令顺序不改变优先级，配置只作用于引用它的清单。

```yaml
schema_version: 1
mappings:
  seq:
    argv: ["+UVM_SEQNAME={value}"]
  image:
    argv: ["+SW_IMAGE={value}"]
  scenario:
    argv: ["+SCENARIO={value}", "+SCENARIO_CASE={case}"]
  sdf:
    argv: ["-sdf", "max:tb.dut:{value}"]
cases:
  custom_case:
    seq: debug_seq
    plusargs: ["+TRACE=1"]
```

mappings 按字段替换内置映射或增加映射；未写出的内置映射继续保留。
argv 是参数数组，一个元素对应一个参数，不经 shell 拼接；允许 `{value}` 和 `{case}`。
选项名必须固定，不能包含占位符；一个映射可生成多个选项。
这些映射仅影响仿真运行命令，不进入编译命令；编译选项由 submit 管理。
同一种语义应使用同一个映射键。修改映射时，也应更新 submit 中手写的旧 plusarg 名称。
镜像路径等普通参数不自动重定位；相对路径以运行目录为基准，可用 env/ 访问工程。
模板与镜像生成、C 编译或加载流程彼此独立；相关输入须登记到 submit.files（按阶段）或 project.inputs 才能追踪内容。
用例字段和 plusargs 可使用 {file:NAME} 引用当前 submit 中声明为 simulate 的输入文件或目录；paths 在 submit 中统一维护。

cases 仅覆盖清单已选择的名称，不引入任务。值是字符串或 YAML null；数字须加引号。
覆盖文件可复用于多个清单，未选择的条目不执行。

## submit 与覆盖优先级

submit 可使用原有 plusargs（VCS 也可使用 vcs.run_args），或增加 case_defaults：

```json
{
  "case_defaults": {"uvm_test": "chip_base_test", "seq": "{case}"},
  "plusargs": ["+STRIDE=1", "+TRACE=0"]
}
```

上面是 submit 片段，需要与正常 schema_version、name、adapter 等配置合用。
case_defaults 的字段由所选 caselist 的 mappings 解释；其 plusargs 仅支持 `{case}`。
submit 原有 argv/plusargs/run_args 保留原有运行占位符，如 `{seed}`、`{result}`。

优先级：submit → 清单 @defaults → 当前用例行 → case.yaml 的 cases 覆盖。
普通单值参数按最终选项键覆盖；plusargs 按 `+KEY` 合并，未提到的键继续保留。
例如低层 `+STRIDE=1 +TRACE=0`，高层 `+STRIDE=2`，最终为 `+STRIDE=2 +TRACE=0`。
多个原生运行参数按选项分组，如 `-sdf` 及其值；一个字段生成多个选项时分别合并。
同一层同键同值去重、不同值报错；重复参数依赖顺序或多值追加的接口需由工程包装器处理。
同层 `uvm_test=a` 与 `+UVM_TESTNAME=b` 会报冲突；高层原始参数可以覆盖低层映射结果。
原生 VCS 的 `+ntb_random_seed` 和 `+NSIM_RESULT` 由 NSIM 生成，不允许重复指定。
command 包装器可在 submit 中用 `+ntb_random_seed={seed}`、`+NSIM_RESULT={result}`
传递这两个值；清单和 YAML 用例覆盖不能改写它们。
这里的逐参数覆盖不同于 submit extends：父子 submit 的数组仍然整体替换。

## 合并、记录与预览

多个清单按顺序选择逻辑名称；defaults/config 不跨文件传播。同名用例在每个 submit 下
有效参数相同时去重，否则规划阶段报错，并显示定义来源。不同参数变体请使用不同逻辑名。
任务键仍是 submit + logic case name + seed；多个 seq 共享 test 时不会被合并。
plan.json 保存清单和 YAML 原文、哈希、解析定义、最终参数及覆盖来源；commands.json 展示
最终仿真命令。整轮回归 recover 校验这些历史输入；rerun 和单跑 recover 读取当前 YAML 和清单配置，保留原任务选择，不增加新 case。整轮 rerun 仅重跑原失败 case/seed，单跑则不按原判定筛选。
case 参数与 YAML 不进入仿真器编译身份，仅修改运行参数可继续使用已有编译产物。

填写真实 RTL/TB filelist 与 submit 后，可在工程根目录预览：

```sh
nsim regr -l caselist/02_seq.caselist -sub submit_demo -seed 42 -o /path/to/output --dry-run
nsim regr -l caselist/06_mappings.caselist caselist/07_overrides.caselist -sub submit_demo -seed 42 -o /path/to/output --dry-run
```

bootstrap 为新项目生成 caselist/ 及本说明。已有同名文件保留；已注册项目重复 bootstrap
保持不变，不自动回填模板。所有 demo 都是可解析示例，实际工程仍需提供对应执行入口。
