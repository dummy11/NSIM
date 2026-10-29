# nsim Hooks 与 VCS/UVM 日志分析

单跑和回归使用同一个阶段执行器、同一个日志分析器。每个 case 生成分析结果，回归只汇总这些结果。日志分析默认在仿真作业的最后执行，不单独提交 LSF job。

## Submit 中配置 hooks

访问仿真环境不需要 hook：nsim 已在编译和用例目录自动创建 `env -> 验证工程根目录`，
在所有 pre hook 之前可用，详见 [环境链接规则](execution_stages.md#仿真环境根目录链接)。

四个阶段分别为 `pre_compile`、`post_compile`、`pre_sim`、`post_sim`。每个阶段接受单个 hook 对象或多个 hook 组成的数组，严格按照声明顺序串行执行；省略时没有用户 hook。下面的对象可作为 submit 的 `hooks` 字段：

```json
{
  "pre_compile": {
    "name": "prepare",
    "argv": ["/usr/bin/python3", "{project}/hooks/prepare.py", "{build_dir}"],
    "timeout_seconds": 30
  },
  "post_compile": {
    "name": "collect_compile",
    "argv": ["/usr/bin/python3", "{project}/hooks/collect_compile.py", "{build_dir}", "{exit_code}"]
  },
  "pre_sim": [
    {"name": "prepare_data", "argv": ["/usr/bin/python3", "{project}/hooks/prepare_data.py", "{case}", "{seed}"]},
    {"name": "check_data", "argv": ["/usr/bin/python3", "{project}/hooks/check_data.py", "{case}"]}
  ],
  "post_sim": [
    {"name": "collect", "argv": ["/usr/bin/python3", "{project}/hooks/collect.py", "{log_dir}"]},
    {"name": "check_result", "argv": ["/usr/bin/python3", "{project}/hooks/check_result.py", "{case}", "{seed}", "{result}"]}
  ]
}
```

这些脚本由工程提供，脚本及依赖文件必须登记在项目 `inputs`。`argv[0]` 是字面的可执行文件路径或命令名，规划时解析并记录身份；其余 argv 支持占位符。不会隐式启动 shell，也不展开 `$VAR` 或命令替换。确实需要 shell 时应显式调用受版本管理的 shell 脚本。

每个 hook 的 `name` 必填，同阶段不可重名；`argv` 非空；`timeout_seconds` 默认 60 秒，必须为正数。工作目录为实际编译目录或 case 仿真目录，环境与该阶段的工具命令一致。hook 不应改变项目输入或在目录之外写入执行产物。

| 占位符 | 可用阶段 | 值 |
| --- | --- | --- |
| `{project}` | 全部 | 验证项目根目录 |
| `{build_dir}` | 全部 | 本次使用的编译目录 |
| `{log_dir}` | 全部 | 当前编译或 case 仿真目录 |
| `{analysis}` | 全部 | 编译阶段为 `log_analysis.json`；仿真阶段为临时协议文件 `.nsim-log-analysis.json`，最终由 nsim 合入 `run.json.analysis` |
| `{exit_code}` | 全部 | 主工具退出码；尚未执行时为空字符串 |
| `{timed_out}` | 全部 | 主工具是否超时，`1` 或 `0` |
| `{case}`、`{seed}` | 仿真前后 | 本次 case 与 seed |
| `{result}` | 仿真前后 | 结构化检查结果 `result.json` |
| `{wave_dir}` | 仿真前后 | 本次波形目录 |

编译 hooks 不接受 case/seed 等仿真占位符。同一 submit 的编译及其 hooks 在同一输出根执行一次；所有 case 复用产物。`sim` 复用已验证编译，不重新执行编译 hooks。编译 hooks 配置与其可执行文件进入编译身份；仿真 hooks 配置变化本身不要求重编译，但项目声明输入变化仍会使旧编译失效。

## 执行顺序与失败处理

```text
pre_compile hooks → 编译 → post_compile hooks → 内置编译日志分析
pre_sim hooks     → 仿真 → post_sim 用户 hooks → 日志稳定检查 → log_analysis
```

`pre_*` 中某个 hook 失败或超时，后续 pre hooks 和主工具不启动。`post_*` 仍按声明顺序运行，便于清理和收集诊断。某个 post hook 失败不跳过剩余 post hooks；日志分析仍在最后执行。主工具非零退出或超时后也执行后置 hooks。用户主动取消时不再启动新的 hook 命令；强制杀进程或存储不可写时不能保证后置步骤完成。

hook 自身失败会保存命令、退出码、超时标记和独立日志。仿真 hook 失败判为 `HOOK_FAIL`；编译 hook 失败使编译不可复用，并阻止仿真。日志无错误不会覆盖 hook 失败、仿真超时或崩溃。

LSF 下，真实编译阶段及其 hooks 在一个编译 job 内执行；仿真阶段及其 hooks 在一个仿真 job 内执行。`-jobs N` 的名额一直占用到该 job 的后置分析结束并确认终态。local 在当前任务槽内执行相同流程。fake/prebuilt 的编译准备没有真实编译 job，准备 hooks 在控制器本地运行；fake 仿真仍可通过 LSF 执行。

## 覆盖仿真日志分析器

默认在所有用户 `post_sim` hooks 后追加内置 `log_analysis`。要替换它，在 `post_sim` 的最后声明名为 `log_analysis` 的 hook：

```json
{
  "post_sim": [
    {"name": "collect", "argv": ["/usr/bin/python3", "{project}/hooks/collect.py", "{log_dir}"]},
    {
      "name": "log_analysis",
      "argv": ["/usr/bin/python3", "{project}/hooks/my_analyzer.py", "{log_dir}", "{analysis}"],
      "timeout_seconds": 60
    }
  ]
}
```

这会替换内置仿真解析规则，不会再执行一次内置 VCS/UVM 解析。其他 post_sim hooks 全部先执行。`log_analysis` 出现在数组中间、同阶段名称重复，或放进其他阶段，均在规划时拒绝；工具不会自动重排用户数组。自定义分析器必须只读日志、同步结束，并将以下协议写到 `{analysis}`：

```json
{
  "schema_version": 1,
  "complete": true,
  "counts": {"warning": 0, "error": 1, "fatal": 0},
  "diagnostics": [
    {
      "severity": "error",
      "message": "scoreboard mismatch: expected 5, got 6",
      "code": "MISMATCH",
      "source_file": "tb/checker.sv",
      "source_line": 27,
      "log_file": "run.log",
      "log_line": 153
    }
  ]
}
```

四个顶层字段均必填，不接受额外字段；`complete` 为布尔值，三个计数为非负整数。每条诊断必填 `severity`（warning/error/fatal）和 `message`，其他示例字段可省略；行号从 1 开始，字符串最多 4096 字符。计数不得小于对应诊断条数，诊断数量不得超过 `max_diagnostics`，输出文件不得超过 16 MiB。成功但无错误时使用全零计数和空数组。输出缺失、格式错误或 hook 非零退出都不能被当作通过。

工具校验并补充 case/seed、阶段、执行主机和日志身份。仿真阶段只将计数、扫描完整性、首错位置和简短原因返回给控制器，再与 case 最终判定一起原子发布到 `run.json.analysis`；临时协议文件随后清理。脚本必须使用 `{analysis}` 参数，不能写死旧文件名。自定义分析器决定日志错误规则；nsim 仍独立检查进程退出、超时、输入身份和 `result.json` 的必需检查。因此日志通过不会自动证明 Golden 比较或功能检查通过。其他 post_sim hooks 可以先生成 `result.json`，但不应提前生成最终的日志分析结果。

## 内置 VCS/UVM 解析

支持标准 UVM WARNING/ERROR/FATAL 消息（含可选 verbosity、源码文件/行、仿真时间、component、report ID）、UVM severity 汇总，以及 VCS `Warning-[CODE]`、`Error-[CODE]`、`Fatal-[CODE]` 和运行期 `Warning:`/`Error:`/`Fatal:` 格式。VCS 后续行中的 `"file.sv", line` 等位置会关联到前面的诊断；保留最多六行上下文。ANSI 颜色控制符会去除，异常编码按替换字符读取。

UVM 格式依据 [Accellera 官方 report server](https://github.com/accellera-official/uvm-core/blob/main/src/base/uvm_report_server.svh)。源码位置本来就是可选信息，缺失时只提供日志位置，不编造源码位置。自定义 report server、厂商版本特有格式或项目特殊断言格式可使用自定义 log_analysis hook。

解析器不会把 `UVM_ERROR : 0` 或普通 INFO 消息中的 “error” 字样当成错误。同一日志的 UVM 明细数和汇总数取较大值，不重复相加；只有汇总而缺少明细时仍报告错误，但无法给出不存在的源码位置。各输入日志分别计数，如果同一批消息同时镜像到多个文件，应只配置一份主日志，避免镜像重复计数。

ERROR/FATAL 会使 case 失败，即使工具零退出且 result.json 表示通过；WARNING 默认仅报告。扫描不完整时不能判为通过。对于超时、崩溃、hook 失败等情况，保留原有失败分类，同时附上日志中的首个错误原因。

## 日志范围、写入冲突和资源开销

submit 的可选 `log_analysis` 对象：

```json
{
  "files": ["run.log"],
  "compile_files": ["compile.log"],
  "settle_seconds": 0.5,
  "max_diagnostics": 100,
  "max_scan_bytes": 1073741824
}
```

以上即默认值。仿真主命令、pre_sim 和 post_sim 的 stdout/stderr 按执行顺序合入 `run.log`；编译主命令、pre_compile 和 post_compile 同理合入 `compile.log`。stdout/stderr 共用一个追加写句柄，程序自身的输出缓冲仍可能影响显示顺序。主命令不会截断前置 hook 的输出，超时和取消后的已写内容也会保留。

### 已确认错误的精确豁免

submit 可增加 `log_analysis.error_filter`，指向一个独立 JSON 文件。例如：

```json
{
  "log_analysis": {
    "compile_files": ["compile.log"],
    "files": ["run.log"],
    "error_filter": "../rules/error_filter.json"
  }
}
```

路径相对声明该字段的 submit 文件；若由 `extends` 的 common 模板声明，就相对 common 模板。
规则文件内容示例：

```json
{
  "schema_version": 1,
  "rules": [
    {
      "phase": "compile",
      "log_file": "compile.log",
      "code": "KNOWN_TOOL_MESSAGE",
      "message": "reviewed harmless diagnostic",
      "reason": "已核对工具版本和编译产物"
    },
    {
      "phase": "simulate",
      "log_file": "run.log",
      "code": "KNOWN_UVM_ID",
      "message": "reviewed harmless diagnostic",
      "reason": "已核对检查结果"
    }
  ]
}
```

`phase` 必填，取 `compile` 或 `simulate`。`code` 和 `message` 都必须精确相等；`message`
只匹配诊断首行，不包含 VCS 后续追加的源码位置与上下文。`log_file` 可省略，省略时该规则
适用于所选阶段的全部已扫描日志；填写时必须等于 `compile_files` 或 `files` 中的相对路径。
规则只匹配 `error`，不豁免 `fatal`；缺少逐条消息的 `UVM_ERROR : N` 汇总差额仍阻止通过。
自定义分析器只可豁免其明确列出的诊断；仅提供错误计数而没有对应逐条诊断时仍阻止通过。
规则文件缺失、格式不合法或运行过程中内容改变会报错，不能静默跳过。最多 128 条规则，
文件最多 128 KiB；规则文件内容摘要纳入运行计划与输入校验，编译阶段的规则纳入编译身份。

扫描结果保留原始 `error`/`fatal` 计数，并另存 `blocking_counts`、`waived_counts` 和最多 8 条
命中记录（规则序号、日志位置、原因）。即使日志错误全被豁免，进程退出失败、必需检查未执行、
结果文件或日志扫描不完整也不能通过。未配置规则文件时，日志错误继续阻止原本通过的结果。

VCS 原生工具日志独立使用 `-l sim.log` / `-l vcs.log`，默认不再重复扫描；存在时纳入证据校验。command 包装器应遵循相同命名，不要自行覆盖或重定向到 NSIM 管理的主日志。工具若有只写入专用文件的诊断，可显式配置扫描该文件；文件列表整体替换默认值，多个文件包含相同诊断时会重复计数，应按实际内容选择输入或使用自定义分析器。路径相对当前阶段目录，不接受绝对路径、父目录跳转、符号链接或非普通文件。列表中的文件都必须存在并完成扫描；不把缺失文件当成空日志。

旧 submit 显式列出 `launch.log` / `compile.err` 时，会保留空的兼容文件；新的 stderr 已全部写入主日志，不复制一份。建议将旧列表更新为上述默认值。

LSF 的 bsub/bjobs/bkill 命令、stdout/stderr、状态变化、执行主机、worker 结果和 farm.out/farm.err 内容也合入对应主日志，包括提交拒绝、worker 启动失败和排队取消。调度命令带时间戳，原始中转文件保留在 `.lsf-jobs/`，日常排查只需主日志。主日志在提交前创建；worker 追加工具输出；worker 完成分析、控制器确认终态并等待 LSF 输出稳定后，将调度内容追加到尾部，不改变原有错误行号。LSF 内容不再作为工具诊断重复扫描。

合并前验证 worker 记录的主日志哈希，合并后重新记录完整日志和 LSF 原始输出的哈希。缺失、持续变化或超出 `scheduler.result_wait_seconds` 的输出会使日志收集失败，不能发布 PASS。状态未确认的作业保留目录占用并在主日志标记“不完整”；此时后续输出仍可能继续，不能声称完整或安全重跑。

主进程结束后，工具清理同一进程组的残留子进程，flush/fsync 并关闭捕获日志，再执行所有用户后置 hooks。之后检查配置日志的大小、修改时间与文件身份连续稳定，才进入分析。扫描前后再检查身份，并在同一遍读取中计算日志哈希。

若扫描期间日志被追加或替换，内置分析器在 `log_analysis.settle_seconds` 的重试窗口和总读取预算内重新等待并扫描；不会重跑其他 post_sim hooks。持续写入、延迟可见超过预算、缺失文件、扫描超出读取量预算都会得到不完整结果，不能发布 PASS。自定义分析器仅执行一次；运行前后日志变化时结果无效，不擅自重复用户命令。这里的日志稳定窗口与 submit 顶层针对 result.json 的 `settle_seconds` 分开配置。

所有 hooks 必须同步完成，不得留下脱离进程组的后台写入任务；最后的分析 hook 不得修改输入日志。自定义分析器的 stdout/stderr 临时缓冲，输入稳定性校验结束后再追加到主日志尾部并更新证据哈希；其自身输出不作为本次诊断输入，不会因正常打印导致日志变化误报。稳定性检测用于发现竞争与延迟可见，不能替代这个写入所有权约定，也不是跨任意 NFS 故障的分布式事务。

没有额外 LSF 提交或排队开销。默认解析逐行扫描，每阶段最多读取 1 GiB（包含重试）、最多保留 100 条诊断；超过诊断数量后继续统计并保留首个失败，超过扫描量则明确标记不完整。超长行分块处理，摘要长度受限，内存不随完整日志大小增长。扫描成本主要是日志 I/O；并发仍受 `-jobs` 限制。自定义 hooks 的运行时间和资源消耗取决于用户脚本，并受各自超时控制。

回归实时进度按变更任务维护计数，只发布变更分页和小型摘要，不反复扫描已完成日志。最终发布与显式 `report show` 仍校验产物哈希，同一份共享编译证据在一次校验中只读取一次。

每个 case 的 post_sim 分析与执行记录保存后，完成回调唤醒 nsim 主进程，直接更新终端 Dashboard 的该 case 与汇总，无需等整批结束。仅仿真进程退出、post_sim 尚未完成时不会提前结算。显示流程不重跑分析器、不轮询文件，也不提交新 job，详见 [终端 Dashboard](dashboard.md)。

## 输出与回归汇总

```text
com_dir/
  compile.log                   # 主命令与前后处理的 stdout + stderr
  vcs.log                       # 工具 -l 日志（若生成），默认不重复扫描
  hooks.json                    # 有序 hook 记录，含主日志的起止字节偏移
  log_analysis.json             # 编译日志分析，一份供全部 case 引用
  build.json
sim_dir/case_seed/
  run.log                       # 主命令与前后处理的 stdout + stderr
  sim.log                       # 工具 -l 日志（若生成），默认不重复扫描
  hooks.json
  run.json                      # schema 2：任务、attempt、编译关联；analysis 内嵌 schema 3 摘要
  result.json                   # 功能/Golden 等结构化检查
```

编译失败、仿真未启动时，case 的 run.json.analysis 保留编译的计数和首错位置；仿真计数和扫描完整性为 null。没有启动或状态不明的阶段不会伪造日志分析完成记录。存储不可写时会明确报错，无法保证输出文件可生成。

回归 `state.json`、实时/最终报告汇总各 case 的错误/致命错误数量、位置和失败原因，`log_summary` 分开统计编译与仿真计数；共享编译只统计一次。原有失败清单和 rerun/recover 继续使用最终 verdict。分析结果、hook 日志和声明日志纳入证据校验，覆盖或损坏后历史 PASS 会显示为 INCOMPLETE。


## 轻量 case 摘要（schema 3）

每个 case 的 `run.json` 内嵌 `analysis` 字段，保留以下 schema 3 摘要；不再另外生成 `analysis.json`：

```json
{
  "schema_version": 3,
  "case": "conv_basic",
  "seed": 42,
  "verdict": "FAIL",
  "reason": "all required checks passed | log: scoreboard mismatch",
  "result_verdict": "PASS",
  "result_reason": "all required checks passed",
  "first_error": {
    "phase": "simulation",
    "log_file": "/output/demo/rtl/sim_dir/conv_basic_42/run.log",
    "log_line": 153,
    "source_file": "tb/checker.sv",
    "source_line": 27
  },
  "first_blocking": {
    "phase": "simulation",
    "log_file": "/output/demo/rtl/sim_dir/conv_basic_42/run.log",
    "log_line": 153,
    "source_file": "tb/checker.sv",
    "source_line": 27
  },
  "counts": {
    "compile": {"warning": 0, "error": 0, "fatal": 0},
    "simulation": {"warning": 0, "error": 1, "fatal": 0}
  },
  "blocking_counts": {
    "compile": {"error": 0, "fatal": 0},
    "simulation": {"error": 1, "fatal": 0}
  },
  "waived_counts": {
    "compile": {"error": 0, "fatal": 0},
    "simulation": {"error": 0, "fatal": 0}
  },
  "waivers": {"compile": [], "simulation": []},
  "error_filter_sha256": {"compile": null, "simulation": null},
  "scan_complete": {"compile": true, "simulation": true}
}
```

- `reason` 为单行原因，最长 512 字符；多行上下文和重复错误留在原始日志。
- `first_error` 只含首错阶段与已知源码/日志位置，不复制消息正文、report ID 或 component。没有首错或没有可用位置时为 null，不编造位置。
- `first_blocking` 是首条未豁免错误的位置；若没有未豁免错误或位置不可得则为 null。报告和终端优先显示它，`first_error` 仍保留原始首错。
- `counts` 分开记录编译与仿真的 warning/error/fatal 数量，回归共享编译仍只计一次。阶段未获得分析结果时，该阶段计数为 null，不能解释为零错误。
- `result_verdict` 和 `result_reason` 是合并日志之前的执行与结构化检查结论；最终结论仍在 `verdict` 和 `reason`。无仿真结果时前者为 null。
- `blocking_counts` 与 `waived_counts` 将日志原始错误数量分开；`waivers` 记录规则命中摘要，`error_filter_sha256` 标识使用的规则内容。原始日志与 `counts` 不因豁免而改变。
- `scan_complete` 同样按阶段记录：true 表示已完整扫描，false 表示扫描不完整，null 表示未获得分析结果。日志完整且零错误也不能替代功能检查或进程状态。

从 schema 2 迁移：不再保存 `compile`/`simulation` 嵌套报告以及 `diagnostics`、`files`、`issues`、`scan_attempts` 等明细。需要排查时按 `first_error` 返回原始日志；源码位置与日志行号含义不同，均从 1 开始。共享编译的 `com_dir/log_analysis.json` 保持原协议。

`run.json.task.attempts` 和回归最终状态保存小型分析摘要，其 `log_analysis.path` 指向 case 的 `run.json`；原始日志的哈希保留在执行记录的 `evidence` 中。LSF worker 只传递摘要，不再复制完整仿真诊断数组。内置仿真扫描仍统计所有消息（受扫描预算限制）并保留首错，但不累积诊断列表。

自定义分析器的临时四字段输入协议保持 schema 1，仍按 `max_diagnostics` 校验。工具消费该协议后只保留摘要；自定义脚本如产生额外排查明细，应输出到 stdout/stderr，最终会合入主日志，并尽量提供原始日志位置。临时协议文件和输出缓冲文件会清理，不作为独立持久化明细。

控制器发布摘要和执行记录后才发布 case 完成事件。最终 `run.json` 中的分析与原始日志均纳入哈希校验，修改或丢失后历史 PASS 会变为 INCOMPLETE。

单跑把计划和完成状态也合入 `run.json`，用控制信息与分析摘要的独立校验值区分记录损坏：分析损坏可显示 INCOMPLETE 并 recover，控制信息损坏则拒绝重跑。回归通过公共完成记录绑定各 case 的 run.json 哈希；从回归根 recover 可以重建损坏或缺失的 case 记录。
