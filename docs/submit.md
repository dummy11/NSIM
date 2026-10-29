# nsim Submit 完整语法示例

`submit_demo.json` 是默认 RTL/VCS 示例，继承 `common/rtl.json`。
本说明同时给出所有支持的字段与互斥适配器语法。JSON 不支持注释、重复键和 NaN。
示例中的 YOUR_*、脚本、DPI Makefile、共享 Python 路径均需替换为实际配置；不会自动创建。
原生 `adapter=vcs` 当前仅支持 dry-run；实际执行使用工程的 `command` 包装器。
这些模板不证明工程具备 Golden 比较器、波形能力或对应硬件平台。

## 日常使用：只维护差异

在已登记的工程内，先由工程维护者配置公共模板的工具、队列、检查器和平台命令。
日常新增 submit 可以直接使用：

```sh
nsim submit new smoke
nsim submit new gate_debug --mode gate
nsim submit show smoke
nsim submit check smoke
nsim run --case smoke -sub smoke -o /path/to/output --dry-run
```

new 自动生成 submit/NAME.json，填好 schema_version、name 和 extends；不复制父配置，
用户只需添加实际差异。默认选择 RTL，也可通过 --mode 选择其他模式。
指定 --extends 时从父模板推导 mode，同时指定 --mode 时检查一致性。
工程已准备自定义父模板时，可用 `nsim submit new special --extends common/rtl_debug.json`。
父路径相对工程 submit/，也支持绝对路径；不需要相对终端当前目录换算。
旧项目缺少默认 common 时只补齐本次所需模板；已有同名 submit 不覆盖。
未配置真实 command.argv 的硬件模板会生成待填写骨架并明确提示，不能直接运行。
这些命令支持 --project 指定工程，省略时从当前目录及祖先发现工程。

show 展示继承后的有效配置及来源路径。check 检查字段和继承关系；不启动工具、
不创建运行输出、不检查 RTL/TB 或工具是否存在，工程输入和命令预览用 run --dry-run 检查。
运行时可以省略 -mode，由最终 submit 配置推导，默认工作区也使用该模式。
显式 -mode 必须匹配；一次调用的多个 submit 必须使用同一 mode，混合模式应分开运行。
完整字段示例用于查阅，不需要复制到每个 submit 中。

## 目录与 mode

```text
submit/
├── common/
│   ├── rtl.json
│   ├── gate.json
│   ├── fpga.json
│   ├── haps.json
│   ├── zebu.json
│   └── palladium.json
├── submit_demo.json
└── submit_demo.md
```

每个公共模板声明自己的 mode；rtl/gate 默认使用 vcs，其他模式使用 command，并需在子 submit
配置真实 argv。gate 不预置 SDF、库或工艺选项，硬件模式不预置厂商命令。
模板没有 name，不能直接提交。可以增加 common/rtl_debug.json 等自定义父模板。
省略 `-mode` 时使用最终 submit.mode；显式指定时必须匹配，它不动态选择或修改父模板。mode 支持
rtl、gate、fpga、haps、zebu、palladium。新项目 bootstrap 生成上述文件，已有同名文件保留；
已注册项目重复 bootstrap 保持不变，不自动回填或迁移旧模板。

## 继承与覆盖

子文件必须自己声明 schema_version=1 和 name；adapter 可从父文件继承。
extends 是单个文件路径，按当前 submit 文件所在目录解析，也支持绝对路径。
只允许一层继承，父文件不能再声明 extends；拒绝自引用、多父类、缺失父文件及非法配置。
父文件可省略 name 与需要子文件补齐的字段。对象递归合并，标量覆盖，数组整体替换；
[] 表示清空数组，false 和 0 按字段约束正常生效。空对象 {} 不会清空父对象，
null 不是通用删除符号：compile 与 vcs.dpi 的 null 关闭可选阶段；
case_defaults 内的字段 null 用于清除对应运行参数，其余字段按各自类型约束校验。
每个 hook 阶段整体替换，[] 清空该阶段。合并后才补默认值并校验完整配置。
以下示例完整替换 compile_args，保留父模板其他 vcs 字段：

```json
{
  "schema_version": 1,
  "name": "rtl_debug",
  "extends": "common/rtl.json",
  "vcs": {"compile_args": ["-full64", "-sverilog", "-debug_access+all"]},
  "hooks": {"pre_sim": []}
}
```

不带 extends 的独立 submit 仍受支持。name 为 1–128 个安全字符，首字符为字母或数字，
其余可用字母、数字、下划线、点和连字符；regr_dir 是保留名。
父子原文、路径链及哈希和合并后配置记录在 plan.json；父文件改动会阻止旧计划精确重跑。
编译复用根据有效编译配置和依赖计算；只改 run_args 等仿真选项不会单独触发重编译。

## VCS 与通用字段全集

以下字段可按需放进公共模板或具体 submit。默认文件不强加 top、DPI 或自定义 hooks；
本例展示这些可选项的完整语法，采用前必须配置真实工程依赖。

```json
{
  "schema_version": 1,
  "name": "submit_demo",
  "extends": "common/rtl.json",
  "mode": "rtl",
  "adapter": "vcs",
  "required_checks": [
    "functional"
  ],
  "env": {
    "NSIM_SUBMIT_LABEL": "demo"
  },
  "plusargs": [
    "+UVM_VERBOSITY=UVM_LOW"
  ],
  "timeout_seconds": 60,
  "settle_seconds": 0.5,
  "scheduler": {
    "kind": "lsf",
    "resources": [
      "rusage[mem=4096]"
    ],
    "licenses": {
      "YOUR_LICENSE": 1
    },
    "queue": "YOUR_QUEUE",
    "bsub": "bsub",
    "bjobs": "bjobs",
    "bkill": "bkill",
    "worker_python": "/path/to/shared/nsim/venv/bin/python",
    "poll_seconds": 1,
    "deadline_seconds": 3600,
    "cancel_seconds": 30,
    "command_timeout_seconds": 30,
    "unknown_seconds": 30,
    "result_wait_seconds": 5
  },
  "hooks": {
    "pre_compile": {
      "name": "prepare",
      "argv": [
        "../scripts/prepare",
        "{project}",
        "{build_dir}"
      ],
      "timeout_seconds": 60
    },
    "post_compile": [
      {
        "name": "inspect_build",
        "argv": [
          "../scripts/inspect_build",
          "{exit_code}",
          "{timed_out}"
        ],
        "timeout_seconds": 60
      }
    ],
    "pre_sim": [
      {
        "name": "prepare_case",
        "argv": [
          "../scripts/prepare_case",
          "{case}",
          "{seed}",
          "{wave_dir}"
        ],
        "timeout_seconds": 60
      }
    ],
    "post_sim": [
      {
        "name": "check_result",
        "argv": [
          "../scripts/check_result",
          "--result",
          "{result}"
        ],
        "timeout_seconds": 60
      },
      {
        "name": "log_analysis",
        "argv": [
          "../scripts/analyze",
          "{log_dir}",
          "{analysis}"
        ],
        "timeout_seconds": 60
      }
    ]
  },
  "log_analysis": {
    "files": [
      "run.log"
    ],
    "compile_files": [
      "compile.log"
    ],
    "max_diagnostics": 100,
    "max_scan_bytes": 1073741824,
    "settle_seconds": 0.5
  },
  "vcs": {
    "executable": "vcs",
    "compile_args": [
      "-full64",
      "-sverilog",
      "-debug_access+all"
    ],
    "run_args": [
      "+YOUR_RUNTIME_OPTION=1"
    ],
    "compile_timeout_seconds": 600,
    "golden_enabled": false,
    "fsdb": false,
    "fsdb_file": "{wave_dir}/wave.fsdb",
    "fsdb_plusargs": [
      "+fsdb",
      "+NSIM_FSDB={wave}"
    ],
    "result_argv": [
      "../scripts/check_result",
      "--log",
      "{log}",
      "--result",
      "{result}"
    ],
    "verdi": "verdi",
    "filelist": "filelist/sim.f",
    "top": "YOUR_TB_TOP",
    "dpi": {
      "makefile": "model/Makefile",
      "make": "make",
      "cc": "cc",
      "cxx": "c++",
      "scope": "project",
      "target": "all",
      "args": [
        "MODEL=example"
      ],
      "timeout_seconds": 300
    }
  }
}
```

schema_version 当前为 1；adapter 支持 vcs、command、fake。env 是字符串到字符串的映射，
不执行 shell 展开。plusargs、compile_args、run_args、result_argv、资源和文件列表均为字符串数组，
每个元素是一个参数；有空格的参数也放在同一个元素内。无需为新增 VCS 选项修改 nsim。
VCS 编译的 -o、-Mdir、-l，以及仿真的 -l、seed、result 参数由 nsim 生成，勿在自定义数组中重复设置。
仿真主命令与前后处理的 stdout/stderr 合入 run.log，工具原生日志为 -l sim.log；
编译同理合入 compile.log，工具原生日志为 -l vcs.log。默认分析只扫描主日志，避免重复计数。
LSF 提交、查询、取消、worker 输出及作业报告也在阶段结束时合入主日志；日志收集不完整不能通过。
command 包装器也应遵循此约定，不要再自行重定向到 run.log/compile.log。
case 始终是逻辑用例名；UVM test、seq 和 C test 的选择由运行参数决定，清单可以覆盖 submit。
可选 case_defaults 配置示例为 `{"uvm_test": "chip_base_test", "seq": "{case}"}`；
也可以继续通过 plusargs 或 vcs.run_args 配置入口。语义字段由 caselist 的映射解释，
常规单值参数按 submit → 清单默认值 → 当前行 → 可选 case.yaml 用例覆盖的顺序合并。
相同层级字段和原始参数发生冲突时报错。这里的参数合并不改变 extends 的数组整体替换规则。
新 bootstrap 同时生成 caselist/README.md、8 个清单 demo 与显式引用才生效的 case.yaml。
schema 2 使用登记的 RTL/TB filelist；vcs.filelist 可省略或设为 filelist/sim.f。
旧 schema 1 项目必须配置 vcs.filelist。top 可省略。

required_checks 是非空、无重复的检查名数组；默认配置要求 functional。
未显式配置时解析器的兼容默认值为 functional、golden；VCS 要求 golden 时必须显式
golden_enabled=true，并接入实际比较器。golden_enabled 不会自动实现比较。
timeout_seconds 和 compile_timeout_seconds 为正数；settle_seconds 可为 0。

scheduler.kind 支持 local/lsf（默认 lsf）；queue、worker_python 可省略。
resources 为 LSF 资源表达式数组，licenses 为资源名到正整数数量的映射。
bsub/bjobs/bkill/worker_python 指定工具；poll/deadline/cancel/command_timeout/unknown/result_wait_seconds
均为至少 0.01 的秒数，分别控制轮询、总运行预算、取消确认、调度命令、未知状态与结果等待。
local 不调用 LSF 工具。命令行队列覆盖发生在继承合并之后。

fsdb 控制是否附加 fsdb_plusargs，fsdb_file 指定波形路径，verdi 指定查看器。
dpi 可省略或为 null；makefile 相对工程根，make/cc/cxx 指定工具，scope 支持 project/regression/run，
target 为安全名称，args 为传给 make 的附加参数，timeout_seconds 至少 0.01。
result_argv 为结果转换/检查命令；空数组表示不配置该命令。真实结果须满足 nsim 结果协议。

## Command 适配器

command 必须提供非空 argv，可指定已有可执行文件，也可用 {build_dir}/产品名引用声明的编译产物。
compile 仅支持 command，可省略或设为 null；配置时 argv 和 artifacts 必填。
artifacts 是编译目录内的相对路径，不能越界或与 nsim 管理文件重名。
下面演示 FPGA 模板；其他模式可选择相应父模板，或使用独立的 command submit。
FPGA/硬件平台没有通用编译命令，示例脚本必须由工程实现。

```json
{
  "schema_version": 1,
  "name": "command_demo",
  "extends": "common/fpga.json",
  "argv": [
    "{build_dir}/simulator",
    "--case",
    "{case}",
    "--seed",
    "{seed}",
    "--result",
    "{result}"
  ],
  "compile": {
    "argv": [
      "../scripts/build",
      "{project}",
      "{build_dir}"
    ],
    "artifacts": [
      "simulator"
    ],
    "timeout_seconds": 600
  },
  "scheduler": {
    "kind": "local"
  }
}
```

## Fake 适配器

fake 仅用于工具自测；其结果不代表 DUT 验证通过。
outcomes 支持的全部值如下；durations 是 case 到非负秒数的映射。
未指定 case 默认 pass、duration 0.02 秒；fake 设置不能用于其他适配器。
command 的 argv/compile、VCS 的 vcs 和 fake 的专用选项不要混在同一个有效 submit 中。

```json
{
  "schema_version": 1,
  "name": "fake_demo",
  "adapter": "fake",
  "mode": "rtl",
  "scheduler": {
    "kind": "local"
  },
  "required_checks": [
    "functional",
    "golden",
    "performance"
  ],
  "fake": {
    "outcomes": {
      "smoke": "pass",
      "mismatch": "fail",
      "unchecked": "no_compare",
      "absent": "missing",
      "bad_json": "malformed",
      "exit_error": "crash",
      "slow": "performance_fail"
    },
    "durations": {
      "smoke": 0.1,
      "slow": 2
    }
  }
}
```

## Hooks、日志与占位符

编译目录与每个用例目录自动创建 env -> 验证工程根目录，整个仿真环境可通过 env/... 访问，
不需要声明内部目录或编写链接 hook。链接在 pre hook 前就绪，适用于 local 与 LSF；
dry-run 仅预览链接。同名冲突会报错，env 不能声明为编译产物。
编译、仿真及所有 hooks 的进程必须只读访问验证环境：Linux 使用 bubblewrap，macOS 使用
sandbox-exec；执行节点无法建立保护就失败，不降级为可写执行。源工程权限不变，普通终端中的
symlink 本身仍不具备只读属性。输出应写到编译/用例目录，输入追踪仍由 filelist 和 project.inputs 决定。

hooks 支持 pre_compile、post_compile、pre_sim、post_sim。每阶段可为一个 hook 对象或有序对象数组，
每项 name、argv 必填，timeout_seconds 默认 60 且至少 0.01。同一阶段名称不能重复。
post_sim 最后进行日志分析；名为 log_analysis 的自定义 hook 只能放在 post_sim 最后，
它替换内置解析器，必须向 {analysis} 写入 schema_version=1、complete、
counts（warning/error/fatal）和 diagnostics 的分析协议。未声明时使用内置 VCS/UVM 解析器。
仿真 {analysis} 是临时协议文件；case 最终摘要合入 run.json 的 analysis 字段。
单独提供日志分析不会替代 required_checks 要求的结果证据。

log_analysis.files/compile_files 为仿真/编译日志相对路径的非空数组，不能越界或引用分析输出自身。
max_diagnostics 为 1–10000，限制共享编译诊断及自定义分析协议；case 摘要不复制诊断数组。
max_scan_bytes 为正整数，settle_seconds 为非负秒数；截断或扫描失败会标记扫描不完整。
可选 log_analysis.error_filter 指向一个 JSON 规则文件，路径相对声明此字段的 submit（含 common 父模板）。
每条规则必须指定 phase（compile/simulate）、code、单行精确 message 和 reason；可用 log_file
限定为该阶段已配置扫描的日志相对路径。只豁免完整扫描中精确匹配的 error；fatal、未列出的
UVM 汇总计数、未匹配错误和检查未执行/不完整均不能据此通过。原始日志与错误计数保留。
详细诊断查看原始日志。

| 位置 | 支持的占位符 |
|---|---|
| command.argv / command 的 plusargs | project、build_dir、case、seed、result、wave_dir |
| compile.argv | project、build_dir |
| vcs.compile_args | project、build_dir、dpi_library |
| vcs.run_args / result_argv / fsdb_plusargs / fsdb_file / VCS 的 plusargs | project、build_dir、case、seed、result、wave、wave_dir、log、dpi_library |
| 编译 hooks | project、build_dir、log_dir、analysis、exit_code、timed_out |
| 仿真 hooks | 编译 hooks 的全部，加 case、seed、result、wave_dir |

占位符格式为 {case}；使用 {{ 或 }} 表示字面花括号。不支持属性访问或格式转换。
exit_code/timed_out 由对应阶段状态提供；pre hook 尚未执行主命令，不应用它们推断成功。
argv[0]、hook 和工具可执行文件必须为字面路径或 PATH 名称；仅 command.argv[0] 允许声明的
{build_dir}/产物。包含斜线的相对可执行路径以声明它的 JSON 所在目录为基准，
继承 common 中的工具路径不会改用子文件目录。普通参数不自动重定位；推荐用 {project} 指向工程文件。
filelist、DPI Makefile 按工程根解析；需要追踪的脚本、模型和其他依赖应登记到 project.inputs。

填写真实 TB 清单后，在工程目录预览默认示例：

```sh
nsim run --case smoke -sub submit_demo -o /path/to/output --dry-run
```
