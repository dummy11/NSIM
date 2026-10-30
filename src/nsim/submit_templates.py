"""Bootstrap examples and their complete, colocated submit language guide."""

from copy import deepcopy
import json

from nsim.config import MODES


def common_submit(mode):
    result = {
        'schema_version': 1, 'mode': mode,
        'adapter': 'vcs' if mode in ('rtl', 'gate') else 'command',
        'required_checks': ['functional'], 'env': {}, 'plusargs': [],
        'timeout_seconds': 60, 'settle_seconds': 0.5,
        'scheduler': {'kind': 'lsf', 'resources': [], 'licenses': {}},
        'hooks': {'pre_compile': [], 'post_compile': [], 'pre_sim': [], 'post_sim': []},
        'log_analysis': {'files': ['run.log'], 'compile_files': ['compile.log'],
                         'max_diagnostics': 100, 'max_scan_bytes': 1073741824, 'settle_seconds': 0.5},
    }
    if result['adapter'] == 'vcs':
        result['vcs'] = {
            'executable': 'vcs', 'compile_args': ['-full64', '-sverilog'], 'run_args': [],
            'compile_timeout_seconds': 600, 'golden_enabled': False,
            'fsdb_file': '{wave_dir}/wave.fsdb', 'fsdb_plusargs': ['+fsdb', '+NSIM_FSDB={wave}'],
            'result_argv': [], 'verdi': 'verdi',
        }
    return result


DEMO = {
    'schema_version': 1, 'name': 'submit_demo', 'extends': 'common/rtl.json',
}


def file_demo():
    """Concrete companion inputs make the default example fully previewable."""
    value = dict(deepcopy(DEMO), **common_submit('rtl'))
    base = 'submit/demo_inputs/'
    value['files'] = {
        'coverage_hier': {'path': base + 'vcs/coverage.hier', 'phases': ['compile']},
        'xprop_config': {'path': base + 'vcs/xprop.cfg', 'phases': ['compile']},
        'partition_config': {'path': base + 'vcs/partition.cfg', 'phases': ['compile']},
        'ucli_script': {'path': base + 'ucli/run.tcl', 'phases': ['simulate'],
                        'dependencies': [base + 'ucli/includes/*.tcl']},
        'stimuli': {'path': base + 'data', 'phases': ['simulate'], 'snapshot': False},
        'firmware': {'path': base + 'data/boot.hex', 'phases': ['simulate']},
        'checker_config': {'path': base + 'analysis/checker.json', 'phases': ['simulate', 'analysis']},
        'errors': {'path': base + 'analysis/error_filter.json', 'phases': ['analysis'], 'snapshot': True},
        'tcl_sources': {'path': base + 'ucli/**/*.tcl', 'phases': ['simulate']},
    }
    value['case_defaults'] = {'uvm_test': 'YOUR_UVM_TEST', 'seq': '{case}'}
    value['plusargs'] = ['+UVM_VERBOSITY=UVM_LOW', '+NSIM_INPUT_DIR={file:stimuli}', '+NSIM_BOOT_IMAGE={file:firmware}']
    value['vcs']['coverage'] = {
        'compile_args': ['-cm', 'line+cond+fsm+tgl+branch', '-cm_hier', '{file:coverage_hier}'],
        'run_args': ['-cm', 'line+cond+fsm+tgl+branch'],
    }
    value['vcs']['compile_args'] += ['-xprop={file:xprop_config}', '-partcomp',
                                     '+optconfigfile+{file:partition_config}']
    value['vcs']['run_args'] = ['-ucli', '-do', '{file:ucli_script}']
    value['log_analysis']['error_filter'] = '{file:errors}'
    return value


def full_vcs_demo():
    value = file_demo()
    value['env'] = {'NSIM_SUBMIT_LABEL': 'demo'}
    value['scheduler'].update(
        kind='lsf', queue='YOUR_QUEUE', resources=['rusage[mem=4096]'], licenses={'YOUR_LICENSE': 1},
        bsub='bsub', bjobs='bjobs', bkill='bkill', worker_python='/path/to/shared/nsim/venv/bin/python',
        poll_seconds=1, deadline_seconds=3600, cancel_seconds=30,
        command_timeout_seconds=30, unknown_seconds=30, result_wait_seconds=5)
    value['vcs'].update(
        filelist='filelist/sim.f', top='YOUR_TB_TOP',
        dpi={'makefile': 'model/Makefile', 'make': 'make', 'cc': 'cc', 'cxx': 'c++',
             'scope': 'project', 'target': 'all', 'args': ['MODEL=example'], 'timeout_seconds': 300},
        result_argv=['../scripts/check_result', '--log', '{log}', '--result', '{result}', '--config', '{file:checker_config}'])
    value['hooks'] = {
        'pre_compile': {'name': 'prepare', 'argv': ['../scripts/prepare', '{project}', '{build_dir}'],
                        'timeout_seconds': 60},
        'post_compile': [{'name': 'inspect_build', 'argv': ['../scripts/inspect_build', '{exit_code}', '{timed_out}'],
                          'timeout_seconds': 60}],
        'pre_sim': [{'name': 'prepare_case', 'argv': ['../scripts/prepare_case', '{case}', '{seed}', '{wave_dir}'],
                     'timeout_seconds': 60}],
        'post_sim': [
            {'name': 'check_result', 'argv': ['../scripts/check_result', '--result', '{result}'], 'timeout_seconds': 60},
            {'name': 'log_analysis', 'argv': ['../scripts/analyze', '{log_dir}', '{analysis}'], 'timeout_seconds': 60},
        ],
    }
    return value


COMMAND_DEMO = {
    'schema_version': 1, 'name': 'command_demo', 'extends': 'common/fpga.json',
    'argv': ['{build_dir}/simulator', '--case', '{case}', '--seed', '{seed}', '--result', '{result}'],
    'compile': {'argv': ['../scripts/build', '{project}', '{build_dir}'],
                'artifacts': ['simulator'], 'timeout_seconds': 600},
    'scheduler': {'kind': 'local'},
}

FAKE_DEMO = {
    'schema_version': 1, 'name': 'fake_demo', 'adapter': 'fake', 'mode': 'rtl',
    'scheduler': {'kind': 'local'}, 'required_checks': ['functional', 'golden', 'performance'],
    'fake': {'outcomes': {'smoke': 'pass', 'mismatch': 'fail', 'unchecked': 'no_compare',
                          'absent': 'missing', 'bad_json': 'malformed', 'exit_error': 'crash',
                          'slow': 'performance_fail'},
             'durations': {'smoke': 0.1, 'slow': 2}},
}


def pretty(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + '\n'


def guide():
    return '''# nsim Submit 完整语法示例

`submit_demo.json` 是完整的 RTL/VCS 文件管理示例，继承 `common/rtl.json`，显式展示通用字段与文件引用。
配套 `demo_inputs/` 展示 coverage hierarchy、Xprop、partition、UCLI 及子脚本、数据目录、HEX、分析规则。
`examples/vcs_full.json` 展示 DPI、工具、队列、四阶段 hooks 等全部可选配置；
`examples/command.json` 与 `examples/fake.json` 分别展示互斥的适配器语法。
本说明同时给出所有支持的字段与互斥适配器语法。JSON 不支持注释、重复键和 NaN。
demo_inputs 是教学数据，YOUR_*、DUT 层次、工具选项与 TB plusargs 必须按工程修改。
完整参考中的工程脚本、DPI Makefile、共享 Python 路径需自行配置；不会生成假比较器。
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

show 展示继承后的有效配置、来源路径和 resolved_files。check 检查字段、继承及显式 files 的存在性和成员集合；
不启动工具、不创建运行输出、不检查 RTL/TB 或工具是否存在，完整规划用 run --dry-run 检查。
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
├── submit_demo.md
├── demo_inputs/              # 实际随 bootstrap 创建的教学输入
└── examples/                 # vcs_full.json / command.json / fake.json
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
null 不是通用删除符号：compile 与 vcs.dpi 的 null 关闭可选阶段；files.NAME 的 null 删除继承项；
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
父子原文、路径链及哈希和合并后配置记录在 plan.json；父文件改动会阻止整轮回归 recover 使用旧输入恢复；整轮 rerun 重新读取父子配置并强制编译；单跑 rerun/recover 默认复用编译，显式 --rebuild 才重编译。
编译复用根据有效编译配置和依赖计算；只改 run_args 等仿真选项不会单独触发重编译。

## VCS 与通用字段全集

以下字段可按需放进公共模板或具体 submit。submit_demo 展示文件功能；日常 new 仍生成最小差异配置。
默认文件不强加 top、DPI 或自定义 hooks；
本例展示这些可选项的完整语法，采用前必须配置真实工程依赖。

```json
''' + pretty(full_vcs_demo()) + '''```

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

新 submit 不声明 fsdb 布尔开关。单跑 run/sim 默认打开波形，regr 默认关闭；--waves on/off 显式覆盖。
vcs.fsdb_plusargs 和 fsdb_file 仅定义波形参数与文件位置；启用时会附加 plusargs，不修改编译命令或编译身份。
能否生成 FSDB 还取决于 TB 的 dump 逻辑和实际编译选项。旧 submit 的 fsdb 布尔值仍可读取，未指定 --waves 时沿用其值；建议迁移后删除。
vcs.coverage.compile_args/run_args 分别定义覆盖率编译插桩与运行采集参数，不含启用开关。
未填写 -cm 指标的一侧沿用另一侧的指标；两侧都未填写时使用 line+cond+fsm+tgl+branch。
run/sim/com 默认关闭覆盖率，regr 默认打开；--coverage on/off 显式覆盖。单独 sim --coverage on 需要已有的编译产物具备插桩。
旧 submit 放在 vcs.compile_args/run_args 的 -cm 参数会转入覆盖率参数组，因此其单跑默认行为会变化；迁移时应核对命令预览。
--cm-metrics、--cm-hier、--cm-dir 可以分别改写指标、层次文件和运行输出目录；CLI 显式值高于 submit 覆盖率参数。
command 适配器可在顶层 coverage.compile_args/run_args 和 waveform.run_args 配置包装器自己的参数；
开关按相同场景默认值应用这些参数。未配置 recipe 的包装器不会因开关而自动生成覆盖率或波形。
command 的覆盖率编译参数要求声明 compile 步骤；sim 复用时会核对已有编译的插桩状态。
包装器必须识别所配置参数并实际生成产物，nsim 不从开关推断覆盖率文件或波形文件存在。
verdi 指定查看器。
dpi 可省略或为 null；makefile 相对工程根，make/cc/cxx 指定工具，scope 支持 project/regression/run，
target 为安全名称，args 为传给 make 的附加参数，timeout_seconds 至少 0.01。
result_argv 为结果转换/检查命令；空数组表示不配置该命令。真实结果须满足 nsim 结果协议。

## 文件管理：输入、阶段与引用

files 是按逻辑名称组织的输入表；字段 path、phases 必填，dependencies 默认 []，snapshot 默认 true。
path 和 dependencies 始终相对验证工程根，不相对声明它们的 common/ 或子 submit，也接受绝对路径。
不会展开主机环境变量或 case/seed。继承时按逻辑名称递归合并，同名 path 覆盖、数组整体替换，null 删除条目。
files 中只登记实际使用的输入，不需要枚举整个工程；未登记的工程文件仍可通过 env/ 访问，但不因此自动受追踪。

| 字段/写法 | 行为 |
|---|---|
| path: 普通文件 | 校验存在、记录真实路径和 SHA-256 |
| path: 目录 | 递归记录普通文件与目录成员，包含隐藏文件；检测增加、删除和改指向 |
| path: glob 或 dependencies 中的 glob | 规划时展开 *、?、[]、**；空匹配报错，不运行 shell |
| dependencies | 显式补充 Tcl source、脚本 import、Makefile include 等子依赖，可为文件、目录或 glob |
| phases: [compile] | 内容及成员变化影响编译身份 |
| phases: [simulate] | 内容变化使旧计划不可精确重跑，新计划可以复用兼容编译 |
| phases: [analysis] | 追踪 checker/日志规则输入；用于 post_sim 或 result_argv |
| 多个 phases | 同一文件可以服务多个阶段；编译 hook 的分析配置也必须声明 compile |
| snapshot: true | 每文件最多 128 KiB、每 submit 合计最多 1 MiB，保存 base64 内容快照；更大文件仍记录 hash |
| snapshot: false | 只记录身份，不保存内容；适合大型模型、数据或不需复制的文件 |
| {file:NAME} | 展开成工程输入绝对路径；支持嵌入 -xprop=、+optconfigfile+、plusargs 等参数 |

glob 条目用于追踪集合，不能作为一个 {file:NAME} 参数；需要传目录时声明目录 path。
目录树内部的目录符号链接会报错，需将目标单独登记；普通文件链接及入口目录链接记录目标并检测改指向。
输入只读，包含显式登记的工程外文件与目录；输出目录不能与这些输入重叠。
命令行运行过程中仍读取经过校验的源路径，快照用于审计，不自动替换活跃输入或提供离线重跑。

文件引用可用于 compile.argv、command.argv、plusargs、vcs.compile_args/run_args/coverage.compile_args/coverage.run_args/result_argv/fsdb_plusargs、
command 的 coverage.compile_args/run_args 和 waveform.run_args、
四阶段 hooks、DPI Makefile/args，以及 case_defaults、caselist/case.yaml 的值和 CLI plusargs。
pre_compile/post_compile 要求 compile；pre_sim 要求 simulate；post_sim/result_argv 要求 analysis。
未知名称、阶段不匹配、空 glob、缺失文件会报错。argv[0] 可以是完整的 {file:script} 引用，且必须可执行。
文件引用不会执行 shell，不会把文件内容解释为模板；普通花括号仍用 {{ 和 }} 表示。
env、输出路径、工具安装路径和已登记 HDL filelist 不接受文件引用。

log_analysis.error_filter 既支持原来的声明文件相对路径，也支持完整的 {file:errors} 引用，后者要求 analysis。
规则文件已自动追踪，不必再放进 project.inputs。编译规则的有效内容进入编译身份，单改仿真豁免规则不触发重编译；
如果主动把整个规则文件标为 compile，则整个文件都作为编译依赖。

plan.json 保存 files、resolved_files、各阶段 file_hashes、file_snapshots 和实际引用；commands.json 展示文件及展开后 argv。
local 和 LSF 执行节点都检查文件内容与成员集合；整轮回归 recover 要求登记输入匹配历史计划；rerun 使用当前输入创建新计划，并在本次执行期间校验输入一致性。
project.inputs 仍按旧规则纳入编译依赖；同一文件也在 project.inputs 时，运行阶段声明不会缩小其影响范围。
不会静态执行 Tcl/Python/Make 来猜依赖，必须用 dependencies 补齐动态读取的输入。

### 与生成文件、缓存和产物的边界

files 是已经存在的只读输入表。pre_compile 才生成的文件不能预先登记为 files 或已登记 filelist 的源码。
此类流程由 command 包装器先生成再编译；将生成器、模板和初始数据登记为 compile 输入，将需要复用的输出登记为
compile.artifacts。pre_sim 可以把输入复制到当前用例目录后修改；生成器及原始数据登记为 simulate 输入。
四阶段 hooks 和 command 示例见下文；它们不提供通用生成任务 DAG 或自动依赖发现。

partition.cfg 是编译输入，partition library 是可写缓存；VDB、FSDB、profile 和 checkpoint 是输出，不能放进 files。
现有 com 会清理编译工作目录，原生 VCS 只支持 dry-run，因此本机制不宣称实现 partition 持久增量缓存或 coverage 自动合并。
工程 command 包装器管理这些工具流程时，应隔离可写缓存、验证工具/模式兼容性、为并发作业分配独立结果路径。
coverage 静态模型与运行 VDB 的关联、SDF/UPF 注入方式及 profiling 参数必须按实际工具版本验证。

| 常见文件 | 登记阶段与使用位置 |
|---|---|
| cm_hier / Xprop / partition / library 配置 | compile，编译 argv 文件引用 |
| UCLI / 波形控制 Tcl / 固件 / HEX / 激励 / tensor | simulate，运行 argv 或 pre_sim；子脚本写 dependencies |
| SDF / UPF / DPI 库 | 依实际消费阶段声明，编译注入和运行加载不能混为同一种依赖 |
| error_filter / checker 参数 / coverage exclusion / 性能阈值 | analysis，专用字段或分析命令；编译阶段使用时再加 compile |
| reference model / generator / Python import / Make include | 按使用阶段登记源码、模型和附属文件 |
| partition cache / VDB / FSDB / profile / checkpoint | 本次产物；作为下一次输入使用时显式登记，并核对工具及 build 兼容性 |

## Command 适配器

command 必须提供非空 argv，可指定已有可执行文件，也可用 {build_dir}/产品名引用声明的编译产物。
compile 仅支持 command，可省略或设为 null；配置时 argv 和 artifacts 必填。
artifacts 是编译目录内的相对路径，不能越界或与 nsim 管理文件重名。
下面演示 FPGA 模板；其他模式可选择相应父模板，或使用独立的 command submit。
FPGA/硬件平台没有通用编译命令，示例脚本必须由工程实现。

```json
''' + pretty(COMMAND_DEMO) + '''```

包装器支持相应参数时，可按以下完整 submit 配置；示例参数名需替换为包装器的真实接口：

```json
{
  "schema_version": 1,
  "name": "command_profile_example",
  "adapter": "command",
  "required_checks": ["functional"],
  "compile": {"argv": ["build_wrapper", "{project}", "{build_dir}"], "artifacts": ["simulator"]},
  "argv": ["{build_dir}/simulator", "--case", "{case}", "--result", "{result}"],
  "coverage": {"compile_args": ["--instrument-coverage"], "run_args": ["--collect-coverage"]},
  "waveform": {"run_args": ["--dump-wave", "{wave_dir}/wave.fsdb"]}
}
```

## Fake 适配器

fake 仅用于工具自测；其结果不代表 DUT 验证通过。
outcomes 支持的全部值如下；durations 是 case 到非负秒数的映射。
未指定 case 默认 pass、duration 0.02 秒；fake 设置不能用于其他适配器。
command 的 argv/compile、VCS 的 vcs 和 fake 的专用选项不要混在同一个有效 submit 中。

```json
''' + pretty(FAKE_DEMO) + '''```

## Hooks、日志与占位符

编译目录与每个用例目录自动创建 env -> 验证工程根目录，整个仿真环境可通过 env/... 访问，
不需要声明内部目录或编写链接 hook。链接在 pre hook 前就绪，适用于 local 与 LSF；
dry-run 仅预览链接。同名冲突会报错，env 不能声明为编译产物。
编译、仿真及所有 hooks 的进程必须只读访问验证环境：Linux 使用 bubblewrap，macOS 使用
sandbox-exec；执行节点无法建立保护就失败，不降级为可写执行。源工程权限不变，普通终端中的
symlink 本身仍不具备只读属性。输出应写到编译/用例目录，输入追踪由 filelist、project.inputs 和 submit.files 决定。

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
filelist、DPI Makefile 按工程根解析；脚本、模型和其他依赖可用 files 按阶段登记。
原有 project.inputs 保持兼容，作为所有编译共享的保守依赖；运行专用文件建议迁入 files。

填写真实 TB 清单后，在工程目录预览默认示例：

```sh
nsim run --case smoke -sub submit_demo -o /path/to/output --dry-run
```
'''


def scaffold():
    files = {f'common/{mode}.json': pretty(common_submit(mode)) for mode in MODES}
    full = full_vcs_demo()
    full.update(name='vcs_full', extends='../common/rtl.json')
    command = deepcopy(COMMAND_DEMO)
    command['extends'] = '../common/fpga.json'
    files.update({'submit_demo.json': pretty(file_demo()), 'submit_demo.md': guide(),
                  'examples/vcs_full.json': pretty(full), 'examples/command.json': pretty(command),
                  'examples/fake.json': pretty(FAKE_DEMO),
                  'demo_inputs/vcs/coverage.hier': '// Replace tb.dut with your DUT hierarchy.\n+tree tb.dut\n',
                  'demo_inputs/vcs/xprop.cfg': '// Replace tb.dut with your DUT hierarchy.\nmerge = tmerge;\ninstance { tb.dut } { xpropOn };\n',
                  'demo_inputs/vcs/partition.cfg': '// Replace YOUR_DUT with your module; check your VCS release.\npartition cell YOUR_DUT;\n',
                  'demo_inputs/ucli/run.tcl': '# Resolve child scripts relative to this script, not the job cwd.\nsource [file join [file dirname [info script]] includes waves.tcl]\nrun\nquit\n',
                  'demo_inputs/ucli/includes/waves.tcl': '# Add project-specific dump commands after enabling the required debug access.\n',
                  'demo_inputs/data/boot.hex': '00\n',
                  'demo_inputs/analysis/checker.json': pretty({'description': 'Replace with your real checker configuration', 'tolerance': 0}),
                  'demo_inputs/analysis/error_filter.json': pretty({'schema_version': 1, 'rules': []})})
    return files
