# VITS 选路与旋钮口径

VITS 引擎有**两条推理路**，它们的取值口径必须逐字一致，否则会出现「健康检查验 A、
一条路合成 B」这种检查绿而合成炸的组合。本文是这两条路的唯一说明。

- 子进程路：外部 Python 跑 `meapet/tools/vits_infer.py`。
- 进程内路：本进程 torch 跑 `meapet/tts/engines/vits_runtime.py`。

判据本身只有一处实现：`meapet/tts/common.py::resolve_vits_route`。健康检查、
`speak()`、以及引擎 mixin 都读同一个结果（`VitsRoute`），不再各自算一份。

## 1. 判据

`vits_inprocess` 是**三态**：配置里没这个键 = 用户没表态。

| `tts.vits_python` 解析结果 | `tts.vits_inprocess` | 实际路线 | `reason` |
|---|---|---|---|
| 可用解释器 | 缺省 / `false` / `true` | **子进程** | `external_configured`，或 `true` 时 `explicit_inprocess_overridden_by_external` |
| 不可用 | 缺省，且打包版 | 进程内 | `frozen_no_external` |
| 不可用 | 缺省，且源码运行 | 子进程（本进程解释器） | `not_frozen` |
| 不可用 | `true` | 进程内 | `explicit_inprocess` |
| 不可用 | `false` | **两路都没有** | `no_external_python_fallback` |

两条要点：

1. **外部解释器优先于 `vits_inprocess: true`。** 这条优先级是有意的：打包版自带的
   torch 可能加载不了，而用户配的解释器是能用的。所以它**保留**，但不再无声吞掉用户
   的显式声明——见第 4 节。
2. **`vits_python` 解析失败等于没配。** `resolve_external_python` 对三种输入返回
   空串：空串本身、指向 `MeaPet.exe` 自己、路径不在盘上。打包版下这会落到进程内。

`vits_inprocess: false` 且没有可用外部解释器时，两条路都不可用。此时健康检查报红、
`speak()` 直接返回失败并说明原因，**不会**拿 `sys.executable` 去跑脚本——打包版下那
就是 pet exe，只会再开一个桌宠实例。

## 2. 配置键

```json
{
  "tts": {
    "engine": "vits",
    "vits_python": "C:/path/to/python.exe",
    "vits_inprocess": null,
    "vits_model": "",
    "vits_config": "",
    "vits_speaker": "Mea"
  }
}
```

| 键 | 缺省行为 |
|---|---|
| `vits_python` | 空 = 没配。向导里「VITS Python 路径」写的就是这一格 |
| `vits_inprocess` | 缺省 = 打包版无外部解释器时走进程内；`true`/`false` 为显式声明 |
| `vits_model` | 空 = `vits_models/G_latest.pth` |
| `vits_config` | 空 = `vits_models/finetune_speaker.json` |
| `vits_speaker` | 空 = `Mea` |

后三个键由 `MeaTTS` 落成 `_vits_model` / `_vits_config` / `_vits_speaker` 属性，
两条路都从 `TtsVitsMixin._vits_knobs()` 取值，并且都透传给下游：进程内走
`synthesize_vits(model_path=, config_path=, speaker=)`，子进程走 argv 上的
`--model` / `--config` / `--speaker`。

模型位置常量只有一份（`DEFAULT_VITS_MODEL_NAME` / `DEFAULT_VITS_CONFIG_NAME` /
`DEFAULT_VITS_SPEAKER`）。改模型位置只需要改配置，不要在两处硬编码路径——
健康检查、`speak()` 前置检查、两条路的缺省值都指向配置给出的同一个文件。

## 3. 日志怎么读

选路结果每次 `speak` 都会落一行：

```
VITS route: mode=subprocess reason=external_configured python=python.exe
```

`mode` 只有 `subprocess` 与 `inprocess` 两个值，它**就是**最终裁决。排查时不要照配置
猜，读这一行。

健康检查同源：

```
Health (vits): python=True script=True model=True config=True mode=subprocess reason=external_configured python=python.exe
```

模型或配置验不过时，同一行会带上实际检查的路径，避免「验的和用的不是一个文件」。

## 4. 出声的地方

原设计里这些情况全是静默的，排查时人无从下手。现在每一种都有读数：

| 情况 | 出声 |
|---|---|
| `vits_inprocess: true` 被外部解释器覆盖 | `reason=explicit_inprocess_overridden_by_external` + 一行 warning，说明优先级出处与去掉它的办法 |
| `vits_python` 解析失败 | 构造函数一行 warning，写明是空 / pet exe / 不在盘上，并说明已按未配置处理 |
| 说话人不在模型里 | 一行 warning，列出模型实际可用的说话人名字 |
| 两条路都不可用 | `speak()` 报 error 并说明补哪一格 |

向导侧：保存时校验「VITS Python 路径」，不通过就在状态条上写警告（**照存不误**，
不夺走用户输入）；VITS 模型就绪的状态条上会预告实际会走哪条路。

## 5. 说话人

`finetune_speaker.json` 的 `speakers` 表决定可用说话人。名字不在表里时回落到 0 号
音色，**并留一行 warning**——换错音色不再无声。

判定说话人表一律走鸭子类型，不能写 `isinstance(x, dict)`：经
`vits_core.utils.get_hparams_from_file` 读出来的 `hps.speakers` 是 `HParams` 实例，
它有 `__contains__` / `__getitem__` / `keys`，但不继承 `dict`。

`meapet/tools/vits_infer.py` **不得** `import meapet.*`（冻结版里外部解释器只能看到
落盘的本脚本），所以它自带一份同口径的 `resolve_speaker`。两份实现的一致性由
`tests/test_vits_route_and_knobs.py` 用参数化用例钉住，改一处必须同时改另一处。

## 6. 实测读数（Windows，vits_ft = Python 3.8.20 + torch 2.1.2+cu121）

| | 读数 |
|---|---|
| 子进程整句合成 | 8.0–8.1 s，rc=0 |
| 子进程模型加载（`--warmup`） | 6.9 s |
| 进程内整句合成 | 7.2 s |
| 超时闸 | 90 s，两路都有余量 |
| 随包模型 | `G_latest.pth` 158 MB（非 Git LFS pointer）、`finetune_speaker.json` 说话人表 `{"Mea": 0}` |

### Linux 读数（源码态，2026-10-04 实测）

宿主 `.venv` = Python 3.12.13；外部解释器 = 自造 vits env Python 3.10.21 + torch 2.5.1+cpu
（无 CUDA）。两平台的 torch 形状不同，所以这组数**不能**与上表互换引用。

| | 读数 |
|---|---|
| 端到端子进程合成 | rc=0，整条 27.3 s（含全栈 import）；产物 22050 Hz / 单声道 / float32 / 2.48 s，peak 0.719，幅度 >0.02 的样本占 54.9%（非静音、非削顶） |
| 选路 | `mode=subprocess reason=external_configured python=python`，健康检查与引擎两侧逐字相同 |
| 依赖探针 | `probe_vits_deps` 全栈 import 12–20 s（冷/热页缓存差），90 s 闸有余量；该探针**不落在** `speak()` 路径上，由向导线程代付 |
| 随包模型 | 本机 `vits_models/G_latest.pth` 是 **134 B 的 Git LFS pointer**（仓库无 git-lfs 可用） |
| 回归 | 全量 `1040 passed, 1 skipped` |

两条 Linux 特有的口径，别当成 bug：

1. **权重未水化时不会自动拉取**。显式配 `tts.vits_model` 指向真身，或先 `git lfs pull`。
   向导对 pointer 直接报 error，不再显示"模型就绪（0 MB）"；`speak()` 那一侧同一份文件
   也判不可用，两处判据一致。
2. **进程内那条路在源码态 Linux 不作交付路径**：`vits_inprocess: true` 且没配外部解释器时
   才走它，而本仓库 `.venv` 里没有 torch，这条路只在打包版（自带 torch DLL/so）有意义。

覆盖差一条：`tests/test_vits_route_and_knobs.py::test_real_hps_speakers_is_not_a_dict`
——对**随包真配置**验 `HParams` 鸭子类型的那条——在 Linux 是 skip（本机无 scipy，
`vits_core.utils` 导不进来），目前只有 Windows 真跑过。Linux 侧的等价证据是静态的：
`vits_core/utils.py` 里 `class HParams():` 不继承 `dict`。


## 7. 验收

```bash
python -m pytest -q tests/test_vits_route_and_knobs.py
```

该文件钉死四件事：

1. 判据九格全表（`external_python` × `vits_inprocess` × `frozen`）逐格断言；
2. `force_inprocess` **不得**是惰性的——它一旦参与计算却影响不了结论，测试就红；
3. 两条路的模型 / 配置 / 说话人取值逐字相同，且都进 argv；
4. 两份 `resolve_speaker` 实现（含 `HParams` 形状）解析结果相同。
