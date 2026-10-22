# SWE-bench 开发集与冻结评测集

## 结论

本项目采用 25 个互不重叠的 SWE-bench Verified Python 任务：5 个开发任务允许反复调试，20 个正式评测任务一经预检通过即冻结。这个划分直接落实项目蓝图中的 5/20 隔离和 `6 + 5 + 4 + 3 + 2` 分层要求；它不是 SWE-bench 官方提供的新 split，而是本项目从 Verified 官方 `test` split 派生的本地清单。[SWE-bench 官方数据集指南](https://github.com/SWE-bench/SWE-bench/blob/main/docs/guides/datasets.md)说明 Verified 是 500 个经专家验证、可解决的实例，且 `difficulty` 是 Verified 特有字段。

数据固定为：

- dataset：`princeton-nlp/SWE-bench_Verified`
- config / split：`default` / `test`
- revision：`c104f840cc67f8b6eec6f759ebc8b2693d585d4a`
- 文件：`data/test-00000-of-00001.parquet`
- SHA-256：`a45b1fe4e2f0c8390b2b2938ac83e92ed5979000856808f3679c07812e9e6dcd`

上述字段、任务元数据、人工难度、gold patch 和测试清单均取自[锁定 revision 的官方 Parquet](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified/blob/c104f840cc67f8b6eec6f759ebc8b2693d585d4a/data/test-00000-of-00001.parquet)。表中的“范围”仅用于离线分层，格式为“gold 修改文件数 / hunk 数 / `+新增,-删除`”；运行时不得把 gold patch 或 `test_patch` 暴露给 Agent。官方字段文档也明确把 `patch` 定义为 gold solution，并把 `test_patch`、`FAIL_TO_PASS` 和 `PASS_TO_PASS` 定义为评测数据。[字段说明](https://github.com/SWE-bench/SWE-bench/blob/main/docs/guides/datasets.md#dataset-structure)

## 筛选原则

选择与本项目的 Python-only、Docker Workspace、独立 Evaluator、恢复/停滞检测目标一致：

1. 开发集全部是人工估计 `15 min - 1 hour`、单文件修复，覆盖 Astropy、Django、pytest、SymPy 和 xarray，适合打通 Issue → 搜索 → 修改 → 测试 → Patch 的完整链路。
2. 正式集保持蓝图规定的五种任务形态，而不是只挑最小补丁；长轨迹分层中的两个 `1-4 hours` 实例专门观察重复搜索和停滞恢复。
3. 正式集覆盖 9 个 Python 仓库，避免结果被单一框架或单一测试入口支配。
4. 所有任务都保留原始人工难度；“单点/调用链/边界/兼容性/长轨迹”是本项目依据 issue 行为与 gold 变更范围做的实验分层，并非 SWE-bench 官方标签。

2026-10-22 修订：因本机 TUN 网络使连接超时测试失效、固定镜像缺少 `pytest-httpbin`，将 `psf__requests-2931` 替换为 `matplotlib__matplotlib-22871`。替换前已确认新任务 gold 通过、空补丁实际运行后失败；本地数据使用新的 `swebench-verified-25-20261022` 批次，原批次和 requests 失败报告保留。5/20 划分、五类分层及正式集 9 个仓库覆盖保持不变；其余 24 项的固定任务、镜像及预检证据沿用。

## 开发任务（5）

| Task ID | 版本 | 难度 | 范围 | F2P / P2P | 练习重点 | linux/amd64 镜像（digest 固定） |
|---|---:|---|---:|---:|---|---|
| [`astropy__astropy-12907`](https://github.com/astropy/astropy/issues/12907) | 4.3 | 15 min–1 h | 1 / 1 / +1,-1 | 2 / 13 | 嵌套组合模型的矩阵逻辑 | `docker.io/swebench/sweb.eval.x86_64.astropy_1776_astropy-12907@sha256:7485c1e3c8861efd0c6a4a78b952857592e541031039000d25e9481f045dc4a3` ([tags](https://hub.docker.com/r/swebench/sweb.eval.x86_64.astropy_1776_astropy-12907/tags)) |
| [`django__django-11477`](https://github.com/django/django/issues/11477) | 3.0 | 15 min–1 h | 1 / 1 / +1,-1 | 3 / 151 | 可选 URL 命名组与翻译 | `docker.io/swebench/sweb.eval.x86_64.django_1776_django-11477@sha256:18fa05d1fc555eb673f798a17cfc1a32b86b6cd9f9a56c64b7840cd5e35e7c10` ([tags](https://hub.docker.com/r/swebench/sweb.eval.x86_64.django_1776_django-11477/tags)) |
| [`pytest-dev__pytest-7571`](https://github.com/pytest-dev/pytest/issues/7571) | 6.0 | 15 min–1 h | 1 / 3 / +4,-0 | 1 / 14 | fixture 状态恢复 | `docker.io/swebench/sweb.eval.x86_64.pytest-dev_1776_pytest-7571@sha256:a4cdd92a11a554106948342c614cbcaa9a7fc7f42b261859c9deef149a673cfe` ([tags](https://hub.docker.com/r/swebench/sweb.eval.x86_64.pytest-dev_1776_pytest-7571/tags)) |
| [`sympy__sympy-13647`](https://github.com/sympy/sympy/issues/13647) | 1.1 | 15 min–1 h | 1 / 1 / +1,-1 | 1 / 76 | 矩阵列插入边界 | `docker.io/swebench/sweb.eval.x86_64.sympy_1776_sympy-13647@sha256:d27b2eed2f5d195f63c48acf32a64c562a0121d7ee28effd548702507b4ada8a` ([tags](https://hub.docker.com/r/swebench/sweb.eval.x86_64.sympy_1776_sympy-13647/tags)) |
| [`pydata__xarray-3677`](https://github.com/pydata/xarray/issues/3677) | 0.12 | 15 min–1 h | 1 / 1 / +1,-0 | 1 / 21 | DataArray/Dataset 合并路径 | `docker.io/swebench/sweb.eval.x86_64.pydata_1776_xarray-3677@sha256:97f5f793df1b6df512d8be87cd9c3180c407f858764f0af3d9d98934deca24a9` ([tags](https://hub.docker.com/r/swebench/sweb.eval.x86_64.pydata_1776_xarray-3677/tags)) |

## 正式评测任务（20）

### 单点逻辑错误（6）

| Task ID | 版本 | 难度 | 范围 | F2P / P2P | 评测重点 | linux/amd64 镜像（digest 固定） |
|---|---:|---|---:|---:|---|---|
| [`astropy__astropy-14539`](https://github.com/astropy/astropy/issues/14539) | 5.1 | 15 min–1 h | 1 / 1 / +1,-1 | 2 / 46 | FITS 相等性比较 | `docker.io/swebench/sweb.eval.x86_64.astropy_1776_astropy-14539@sha256:84363223c36a289d101970473e56ad12199e9b5dcbd1deef5568f4fab743ff05` ([tags](https://hub.docker.com/r/swebench/sweb.eval.x86_64.astropy_1776_astropy-14539/tags)) |
| [`django__django-16485`](https://github.com/django/django/issues/16485) | 5.0 | 15 min–1 h | 1 / 1 / +1,-1 | 1 / 9 | `floatformat("0.00")` | `docker.io/swebench/sweb.eval.x86_64.django_1776_django-16485@sha256:beb779e5314f19ed69da3214744fda2b26025511054dc7f367d617f88b216966` ([tags](https://hub.docker.com/r/swebench/sweb.eval.x86_64.django_1776_django-16485/tags)) |
| [`pydata__xarray-4695`](https://github.com/pydata/xarray/issues/4695) | 0.12 | 15 min–1 h | 1 / 1 / +1,-1 | 1 / 875 | 名为 `method` 的维度 | `docker.io/swebench/sweb.eval.x86_64.pydata_1776_xarray-4695@sha256:93a1265e6fe32cda6fe8d5fb5f8eba4d689fedeaae1228abc7948f2403de98a5` ([tags](https://hub.docker.com/r/swebench/sweb.eval.x86_64.pydata_1776_xarray-4695/tags)) |
| [`pylint-dev__pylint-7080`](https://github.com/pylint-dev/pylint/issues/7080) | 2.15 | 15 min–1 h | 1 / 1 / +1,-0 | 1 / 120 | 递归模式的忽略路径 | `docker.io/swebench/sweb.eval.x86_64.pylint-dev_1776_pylint-7080@sha256:d81b3855816c07747fbfd694352ce24fb8152cec70d136b292de55ff6afe2db6` ([tags](https://hub.docker.com/r/swebench/sweb.eval.x86_64.pylint-dev_1776_pylint-7080/tags)) |
| [`scikit-learn__scikit-learn-14053`](https://github.com/scikit-learn/scikit-learn/issues/14053) | 0.22 | 15 min–1 h | 1 / 1 / +2,-1 | 1 / 5 | 单特征树文本导出 | `docker.io/swebench/sweb.eval.x86_64.scikit-learn_1776_scikit-learn-14053@sha256:5a0a67e6c20dc0ec7587d1efa8ae25c3b3775be682e67df94767c68b388a6d42` ([tags](https://hub.docker.com/r/swebench/sweb.eval.x86_64.scikit-learn_1776_scikit-learn-14053/tags)) |
| [`sympy__sympy-20801`](https://github.com/sympy/sympy/issues/20801) | 1.8 | 15 min–1 h | 1 / 2 / +2,-2 | 1 / 96 | 浮点零与布尔假值 | `docker.io/swebench/sweb.eval.x86_64.sympy_1776_sympy-20801@sha256:c2a2bea936145e76f1674773b1174a7f71bf5a498889e8242bb4e9f7c76e8237` ([tags](https://hub.docker.com/r/swebench/sweb.eval.x86_64.sympy_1776_sympy-20801/tags)) |

### 多文件或调用链（5）

| Task ID | 版本 | 难度 | 范围 | F2P / P2P | 评测重点 | linux/amd64 镜像（digest 固定） |
|---|---:|---|---:|---:|---|---|
| [`django__django-11734`](https://github.com/django/django/issues/11734) | 3.0 | 15 min–1 h | 3 / 3 / +4,-6 | 1 / 275 | `OuterRef`、排除查询与模型链路 | `docker.io/swebench/sweb.eval.x86_64.django_1776_django-11734@sha256:5ddbc111ccc7ed091855034eacff1c58347df158d6acd0c015feab17587d36a2` ([tags](https://hub.docker.com/r/swebench/sweb.eval.x86_64.django_1776_django-11734/tags)) |
| [`django__django-16032`](https://github.com/django/django/issues/16032) | 4.2 | 15 min–1 h | 2 / 5 / +3,-8 | 2 / 77 | `annotate` → `alias` → `__in` | `docker.io/swebench/sweb.eval.x86_64.django_1776_django-16032@sha256:2b05c0e1e5815995e627e3780e332cafad8b06acb1fee06a9b7db4e2cff18066` ([tags](https://hub.docker.com/r/swebench/sweb.eval.x86_64.django_1776_django-16032/tags)) |
| [`pytest-dev__pytest-8399`](https://github.com/pytest-dev/pytest/issues/8399) | 6.3 | 15 min–1 h | 2 / 5 / +5,-5 | 1 / 59 | unittest 类 fixture 生命周期 | `docker.io/swebench/sweb.eval.x86_64.pytest-dev_1776_pytest-8399@sha256:b9a5d37ff455b7ecc581bc9432bab3f7e2c73ed429539a7230cee50a4b79beb4` ([tags](https://hub.docker.com/r/swebench/sweb.eval.x86_64.pytest-dev_1776_pytest-8399/tags)) |
| [`sphinx-doc__sphinx-8551`](https://github.com/sphinx-doc/sphinx/issues/8551) | 3.4 | 15 min–1 h | 2 / 2 / +3,-0 | 1 / 32 | 类型解析与交叉引用 | `docker.io/swebench/sweb.eval.x86_64.sphinx-doc_1776_sphinx-8551@sha256:0cee9556d1dab507161552be8bd82757ee1a835fe660a832a6df023bcb60b67d` ([tags](https://hub.docker.com/r/swebench/sweb.eval.x86_64.sphinx-doc_1776_sphinx-8551/tags)) |
| [`scikit-learn__scikit-learn-25102`](https://github.com/scikit-learn/scikit-learn/issues/25102) | 1.3 | 1–4 h | 2 / 7 / +22,-7 | 2 / 59 | transformer 输出的 DataFrame dtype | `docker.io/swebench/sweb.eval.x86_64.scikit-learn_1776_scikit-learn-25102@sha256:b9d4474791bd7ebf1c5bbab468b77802d1fe7368819ff9c9e97076d8cb736472` ([tags](https://hub.docker.com/r/swebench/sweb.eval.x86_64.scikit-learn_1776_scikit-learn-25102/tags)) |

### 边界条件和测试相关（4）

| Task ID | 版本 | 难度 | 范围 | F2P / P2P | 评测重点 | linux/amd64 镜像（digest 固定） |
|---|---:|---|---:|---:|---|---|
| [`django__django-16667`](https://github.com/django/django/issues/16667) | 5.0 | 15 min–1 h | 1 / 1 / +2,-0 | 2 / 24 | 日期控件溢出边界 | `docker.io/swebench/sweb.eval.x86_64.django_1776_django-16667@sha256:a0f59024db622ca13d7b34fe9356230a199588e8895a958e3501ea39251368a6` ([tags](https://hub.docker.com/r/swebench/sweb.eval.x86_64.django_1776_django-16667/tags)) |
| [`matplotlib__matplotlib-22871`](https://github.com/matplotlib/matplotlib/issues/22871) | 3.5 | 15 min–1 h | 1 / 2 / +4,-2 | 1 / 88 | 不跨一月的短日期范围年份标注 | `docker.io/swebench/sweb.eval.x86_64.matplotlib_1776_matplotlib-22871@sha256:0b50468d5fab27e15ed1d19f001d357534872b4392b71683e400923881c258aa` ([tags](https://hub.docker.com/r/swebench/sweb.eval.x86_64.matplotlib_1776_matplotlib-22871/tags)) |
| [`pytest-dev__pytest-7236`](https://github.com/pytest-dev/pytest/issues/7236) | 5.4 | 15 min–1 h | 1 / 4 / +8,-3 | 1 / 51 | `--pdb` 下 skip/tearDown 生命周期 | `docker.io/swebench/sweb.eval.x86_64.pytest-dev_1776_pytest-7236@sha256:edcc72fd067a1b88f55fc9676cd2fa8ad6f0e624a3ae7d92eae232b7f39efadf` ([tags](https://hub.docker.com/r/swebench/sweb.eval.x86_64.pytest-dev_1776_pytest-7236/tags)) |
| [`pydata__xarray-3151`](https://github.com/pydata/xarray/issues/3151) | 0.12 | 15 min–1 h | 1 / 1 / +7,-8 | 1 / 66 | 非单调相同坐标合并 | `docker.io/swebench/sweb.eval.x86_64.pydata_1776_xarray-3151@sha256:320eea88d5a1b8553e7bf3f51c705b2230936800bac59f9cc0acd920a5d44d6b` ([tags](https://hub.docker.com/r/swebench/sweb.eval.x86_64.pydata_1776_xarray-3151/tags)) |

### 依赖理解与兼容性（3）

| Task ID | 版本 | 难度 | 范围 | F2P / P2P | 评测重点 | linux/amd64 镜像（digest 固定） |
|---|---:|---|---:|---:|---|---|
| [`django__django-14349`](https://github.com/django/django/issues/14349) | 4.0 | 15 min–1 h | 1 / 2 / +3,-0 | 1 / 18 | Python URL 修复后的 validator 兼容性 | `docker.io/swebench/sweb.eval.x86_64.django_1776_django-14349@sha256:cf38516897206f8b489543ed8e792249af107f5767e6148287636c5e195acb7b` ([tags](https://hub.docker.com/r/swebench/sweb.eval.x86_64.django_1776_django-14349/tags)) |
| [`sphinx-doc__sphinx-8120`](https://github.com/sphinx-doc/sphinx/issues/8120) | 3.3 | 15 min–1 h | 2 / 3 / +6,-3 | 1 / 44 | gettext locale 目录与加载路径 | `docker.io/swebench/sweb.eval.x86_64.sphinx-doc_1776_sphinx-8120@sha256:1a40447f75c5833ce370d51be4b22d4ef7501e800600834131a3c69fba60cb3f` ([tags](https://hub.docker.com/r/swebench/sweb.eval.x86_64.sphinx-doc_1776_sphinx-8120/tags)) |
| [`astropy__astropy-7671`](https://github.com/astropy/astropy/issues/7671) | 1.3 | 15 min–1 h | 1 / 2 / +9,-0 | 1 / 3 | prerelease 版本与 `minversion` | `docker.io/swebench/sweb.eval.x86_64.astropy_1776_astropy-7671@sha256:e4a644271876ead58b4e1cd05780e0ddb17ca421bf7b2f6f5dca9269d574e201` ([tags](https://hub.docker.com/r/swebench/sweb.eval.x86_64.astropy_1776_astropy-7671/tags)) |

### 长轨迹或容易停滞（2）

| Task ID | 版本 | 难度 | 范围 | F2P / P2P | 评测重点 | linux/amd64 镜像（digest 固定） |
|---|---:|---|---:|---:|---|---|
| [`django__django-15957`](https://github.com/django/django/issues/15957) | 4.2 | 1–4 h | 1 / 4 / +26,-7 | 4 / 89 | sliced queryset 的 prefetch；长推理与回归面 | `docker.io/swebench/sweb.eval.x86_64.django_1776_django-15957@sha256:7e2694f891d96ec8eaef27b746823acaa6dd28d65832b79db2bd18a816b3437e` ([tags](https://hub.docker.com/r/swebench/sweb.eval.x86_64.django_1776_django-15957/tags)) |
| [`sympy__sympy-17630`](https://github.com/sympy/sympy/issues/17630) | 1.5 | 1–4 h | 1 / 1 / +2,-0 | 2 / 19 | 含 ZeroMatrix 的 BlockMatrix 乘法；定位易绕行 | `docker.io/swebench/sweb.eval.x86_64.sympy_1776_sympy-17630@sha256:950c79918a3185786c91b207a0fc7cb9d0d5513a0ef4bf81f0ec2e2f07340a21` ([tags](https://hub.docker.com/r/swebench/sweb.eval.x86_64.sympy_1776_sympy-17630/tags)) |

## 镜像与架构说明

SWE-bench 的官方代码按 `sweb.eval.{arch}.{instance_id.lower()}:latest` 生成实例镜像名；使用远端 namespace 时，还会把 `__` 替换成 `_1776_`。[官方 `TestSpec.instance_image_key`](https://github.com/SWE-bench/SWE-bench/blob/main/swebench/harness/test_spec/test_spec.py) 本清单采用官方 Docker Hub `swebench` namespace，并把每个地址固定到 2026-09-15 API 返回的 `linux/amd64` manifest digest；同日 25 个 `latest` tag 查询均返回 HTTP 200。[官方 Docker Hub 组织](https://hub.docker.com/u/swebench)

这些是 `x86_64` / `linux/amd64` 镜像。官方 harness 文档推荐 x86_64，并把 arm64 支持标为实验性；资源建议是至少 120 GB 空闲磁盘、16 GB 内存和 8 核 CPU。[Harness 资源要求](https://github.com/SWE-bench/SWE-bench/blob/main/docs/reference/harness.md#resource-requirements) 在 Apple Silicon 上必须显式验证 `--platform linux/amd64` 的仿真性能，正式评测优先使用 x86_64 Linux。当前主机已使用首个开发任务镜像完成 Docker Workspace 创建、`/testbed` 写入、patch 导出和容器清理的冒烟验证；25 项 gold-run 预检尚未执行。

## 冻结前的复现与预检

下载锁定数据并校验：

```bash
curl -L \
  'https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified/resolve/c104f840cc67f8b6eec6f759ebc8b2693d585d4a/data/test-00000-of-00001.parquet' \
  -o swebench-verified-c104f840.parquet
shasum -a 256 swebench-verified-c104f840.parquet
```

对任一 digest 地址做架构预检（以下用首个开发任务示例）：

```bash
IMAGE='docker.io/swebench/sweb.eval.x86_64.astropy_1776_astropy-12907@sha256:7485c1e3c8861efd0c6a4a78b952857592e541031039000d25e9481f045dc4a3'
docker pull --platform linux/amd64 "$IMAGE"
docker image inspect "$IMAGE" --format '{{.Os}}/{{.Architecture}} {{.RepoDigests}}'
docker run --rm --platform linux/amd64 "$IMAGE" true
```

安装官方 harness 后，先对全部 25 项运行 gold preflight，再冻结正式集。官方 quickstart 使用 `gold` 预测验证安装；官方仓库当前也保存了一份 500/500 resolved 的 gold 报告，因此本地 gold 失败应先按环境/镜像/harness 问题处理，而不应直接替换任务。[官方 quickstart](https://github.com/SWE-bench/SWE-bench/blob/main/docs/guides/quickstart.md#check-that-your-evaluation-setup-is-correct) · [官方 gold 报告](https://github.com/SWE-bench/SWE-bench/blob/main/gold.fresh-verified.json)

```bash
python -m swebench.harness.run_evaluation \
  --dataset_name princeton-nlp/SWE-bench_Verified \
  --predictions_path gold \
  --max_workers 1 \
  --instance_ids astropy__astropy-12907 django__django-11477 \
  --run_id miniagent-gold-preflight
```

正式实验必须遵循以下冻结规则：

- 只用 5 个开发任务调 Prompt、预算、工具与恢复策略；任何正式评测结果都不得反馈到实现或 Prompt。
- Baseline 与 Recoverable 使用同一模型版本、Prompt、工具、每任务 digest、预算、并发和超时；每个实例至少保存 patch、完整 trace、测试日志与资源指标。
- Task Loader 只向 Agent 提供 `problem_statement` 和 base snapshot，不提供 `patch`、`test_patch`、`FAIL_TO_PASS` 或 `PASS_TO_PASS`；这些字段只进入独立 Evaluator。
- 25 个 gold preflight 全部通过、Docker 架构确认后，把本文件和实验配置一起提交；此后失败只记录和诊断，不临时换题。
- `latest` 只用于发现/人工核对，实际运行始终使用表中的 digest 地址。若官方镜像更新，应建立一版新清单和新实验批次，不能在同一批结果中混用 digest。

## 已知风险

- 旧版 harness 曾有人报告 `astropy__astropy-7671` 和 `pylint-dev__pylint-7080` 的 gold 运行不稳定；官方仓库当前的 fresh gold 报告则记录 500/500 resolved。两条证据并不矛盾，说明冻结前必须在本项目的实际主机、Docker 和 harness 版本上完成 gold preflight。[旧版稳定性报告](https://github.com/SWE-bench/SWE-bench/issues/294) · [当前官方 gold 报告](https://github.com/SWE-bench/SWE-bench/blob/main/gold.fresh-verified.json)
- 旧式镜像可能保留可查询的 Git 历史，造成未来提交或 gold 信息泄漏；评测 Workspace 应限制 `git log`、remote/ref 等旁路，并优先跟进官方 v5 task-repo 流程。[SWE-bench 官方问题说明](https://github.com/SWE-bench/SWE-bench/issues/465)
- `latest` tag 会变化，所以本清单提供并要求使用平台 digest。registry 的 HTTP 200 只证明 tag 元数据当时存在，不证明本地主机一定能拉取、运行或在预算内完成测试。
