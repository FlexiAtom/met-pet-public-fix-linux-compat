# VITS 旋钮口径分歧（社区提案）Windows 侧收尾总结

**面向**：Neko_mea（转交人）
**提案原件**：`vits-windows-knobs-divergence.md`（slug 同名，kind=proposal，status=community，作者 pid-1236927）
**收尾提交**：`b6c7ae0` fix(tts): VITS 选路判据收敛成一处，`vits_inprocess` 不再被无声吞掉
**仓库 / 分支**：`github.com/suan-11/mea-pet-public` @ `main`（已推送，工作树干净）
**写件日期**：2026-10-05

---

## 0. 一句话结论

提案点名的四条缺陷（D1–D4）**已全部修掉并随 `b6c7ae0` 推送**，Windows 侧真机读数
（W1–W3）**已取到并写进提交与文档**；四个待裁项（C1–C4）都已按提案给出的选项落地。
唯一没做完的是 **W4 的探针耗时**——本仓库里既没有 `probe_vits_deps` 也没有
`vits_infer.py --check-deps`，详见 §5，这是留给维护者/提案人的一条待办。

换句话说：这份提案现在**没有阻塞项**，剩下的是一条"要么补量具、要么明确不要"的裁决。

---

## 1. 根因（提案的判断经复核成立）

VITS 有两条推理路，两条路的取值口径不同，而选路判据是恒真式：

```python
# 修复前 meapet/tts/engines/vits.py:34-42
force_inprocess = bool(getattr(self, "_vits_inprocess", False))
prefer_subprocess = bool(external_py) and not (force_inprocess and not external_py)
if external_py:
    prefer_subprocess = True          # ← 这一行把上面整个盖掉
```

穷举 `external_py × force_inprocess` 四格，`force_inprocess` 改变不了任何一格：
它**参与计算却影响不了结果**。`meapet/tts/service.py` 里是同一恒等式的第二个实例，
于是健康日志可以自称 `mode=subprocess` 而引擎实际走另一条路。

修复做法是把判据收敛到**唯一一处** `meapet/tts/common.py::resolve_vits_route`，
健康检查、`speak()`、引擎 mixin 都读同一个 `VitsRoute`。

---

## 2. 四条缺陷的处置

| | 提案描述的缺陷 | 处置 | 按的是哪个选项 |
|---|---|---|---|
| **D1** | `tts.vits_inprocess: true` 被无声吃掉 | 三态化（缺键=没表态）；外部解释器优先**保留**，但覆盖显式声明时落 `reason=explicit_inprocess_overridden_by_external` + 一行 warning；另修 `vits_inprocess=false` 且无外部解释器时拿 `sys.executable` 跑脚本（打包版下那就是 pet exe，只会再开一个桌宠实例），改为健康检查报红 + `speak()` 报错 | **C1 丙**（不改语义、只在被覆盖时出声） |
| **D2** | 向导保存 `vits_python` 不校验 | 保存前校验并写进 VITS 状态条（**照存不误**，不夺走用户输入）；载入时再留一行日志写明是空 / pet exe / 不在盘上 | **C2 乙**（存但出声） |
| **D3** | `_vits_model/_vits_config/_vits_speaker` 只有读者没有写者 | 由配置落成属性，两条路共用 `_vits_knobs()`，并把 `--model/--config/--speaker` **透传进子进程 argv**；健康检查与 speak 前置检查改验引擎真正会用的那两个路径 | **C3 接生产者 + 透传**（改动面大的那支） |
| **D4** | 说话人没有通路，换错音色是静默的 | 名字不在 `finetune_speaker.json` 里时回落到 0 号**并出声**，列出模型实际可用的说话人；三个新配置键 `vits_speaker` / `vits_model` / `vits_config` 把通路打开 | **C4 做了用户可配 + 出声**（比"至少该出声"更进一步） |

提案里点名的三处"加重项"逐条核对：

- `service.py` 的第二个恒等式实例 —— 已删，健康日志改由 `route.describe()` 给出。
- `vits_runtime.py:3` 那句与行为相反的模块文档 —— 已改对（现在写明判据在
  `resolve_vits_route`，外部解释器优先且覆盖会出声）。
- 向导里没有这个字段 —— 复核成立：`grep -rn vits_inprocess wizard/` 仍只命中一句
  docstring 说明；与提案所述旧状态对照，`config.json`（:99）当时也只有
  `"vits_python": ""`、无 `vits_inprocess` 键。新键进了 `config.example.json`
  （五个 `vits_*` 键，该文件此前一个都没有），`config.json` 保持用户本机实配不动。

---

## 3. 请测 W1–W4 的收口

| | 提案要求 | 状态 | 读数 / 说明 |
|---|---|---|---|
| **W1** | `vits_inprocess: true` 且不配 `vits_python`，进程内那条路在 Windows 是否还活着 | ✅ 已测 | 进程内整句 **7.2 s** 合成成功 ⇒ 这条路**是活的**，`force_inprocess` 的语义**保留**，按 C1 走"保留 + 出声"而不是剪键 |
| **W2** | `vits_python` 填主程序自己（`MeaPet.exe`）保存后的真实形状 | ✅ 已测 | `resolve_external_python` 判 pet exe → 按"没配"处理；构造函数落一行 `tts.vits_python=… 不是可用的 Python 解释器`，向导保存时状态条当场提示。打包版下回落到进程内 |
| **W3** | Windows 整合包布局与说话人集合 | ✅ 已测 | `vits_models/G_latest.pth` **158 MB / 158910108 B**，文件头是 `PK…G_latest/data.pkl`（torch 归档，**非 Git LFS pointer**）；`finetune_speaker.json` 的 `speakers = {"Mea": 0}`、`n_speakers = 1` ⇒ 提案担心的"没有 Mea 导致 D4 静默生效"**不成立**，但"换错名字静默换音色"这条通路已按 D4 修掉 |
| **W4** | `probe_vits_deps` 在 Windows 的耗时与三档结论是否可达 | ❌ **未做** | 见 §5 |

**合成的实测耗时**（Windows，`vits_ft` = Python 3.8.20 + torch 2.1.2+cu121）：

| 项 | 读数 |
|---|---|
| 子进程整句合成 | 8.0–8.1 s，rc=0 |
| 子进程模型加载（`--warmup`） | 6.9 s |
| 进程内整句合成 | 7.2 s |
| 超时闸 | 90 s，两路都有余量 |

---

## 4. 代码改动清单

`b6c7ae0` 共 12 个文件、+1463 / −93：

| 文件 | 改动 |
|---|---|
| `meapet/tts/common.py` | 新增判据唯一来源：`VitsRoute`、`resolve_vits_route`、`resolve_vits_speaker`、`vits_model_path`/`vits_config_path`、三个默认常量 |
| `meapet/tts/engines/vits.py` | 选路改读宿主判据；`_vits_knobs()` 两路共用；`--model/--config/--speaker` 透传进 argv |
| `meapet/tts/service.py` | 恒等式第二个实例删除；`vits_inprocess` 三态解析；三个旋钮落成属性；健康检查与 speak 前置检查改验实际路径 |
| `meapet/tts/engines/vits_runtime.py` | 模块文档改对；说话人解析出声 |
| `meapet/tools/vits_infer.py` | 新增 `--model/--config/--speaker` 处理；`resolve_speaker` 与交付码同口径 |
| `wizard/page_tts_vits.py` | D2 保存前校验 + 状态条警告 + 路线预告 |
| `wizard/app.py` | 保存路径接上校验报告 |
| `config.example.json` | 补上 5 个 `vits_*` 键（此前一个都没有） |
| `tests/test_vits_route_and_knobs.py` | 新增，696 行 |
| `docs/tts-vits-routing.zh-CN.md` | 新增技术文档（判据表、配置键、日志读法、实测读数、验收命令） |
| `docs/troubleshooting.zh-CN.md` | 补「填了解释器却不走子进程 / 填了 `vits_inprocess: true` 却没生效」一节 |
| `docs/backend-and-control.md` | 第 6 节加指针 |

---

## 5. W4 的缺口（本件唯一未清项）

提案的约束之一是：

> 就绪判据的单一来源是交付脚本自己（`meapet/tools/vits_infer.py --check-deps`），
> 别再另列包清单。

**本仓库不存在这个开关。** `grep -rn "check-deps\|check_deps\|probe_vits_deps"` 全仓
零命中，只有 `wizard/page_tts_vits.py::_ensure_vits_deps` 一处相关实现，而它：

- 只查 `soundfile` / `scipy` / `librosa` 三个模块，**不加载 torch**；
- 自己维护一份包清单，与"单一来源"的要求相反；
- 没有三档结论（提案要的那个"三档"），也没有 90 s 闸。

因此：

1. **W4 的探针耗时读数不存在。** 上面 §3 表里 6.9–8.1 s 全是合成/加载耗时，**不能**
   当作 `probe_vits_deps` 耗时顶替；
2. 提案说的"就绪判据落在交付码"这条约束，在本仓库**尚未成立**——要么补上
   `vits_infer.py --check-deps`（含三档结论），要么明确它归 Linux 侧那份交付件、
   Windows 侧不需要。

这条不是我能在"复核已交付工作"范围内替你裁决的，故如实列出而不擅自实现。若你要办，
建议的形状是：`vits_infer.py --check-deps` 返回三档 + 非零退出码，向导
`_ensure_vits_deps` 改为调用它而不是自带清单。

---

## 6. 顺手抓到的一个提案没抓到的真 bug（HParams）

提案件说 `speaker_ids.get(speaker, 0)` 会静默换 0 号音色。修的时候发现**它从来没执行过**：

```python
# 老代码恒为假
isinstance(speaker_ids, dict)
```

`finetune_speaker.json` 经 `vits_core.utils.get_hparams_from_file` 读出来以后，
`hps.speakers` 是 `HParams` 实例 —— 它有 `__contains__` / `__getitem__` / `keys`，
**但不继承 `dict`**。所以老代码直接落 `speaker_id = 0`，`--speaker` 一直是死的。
判定改成鸭子类型后这条通路才真正开始工作，两份实现（交付码与
`vits_infer.py`，后者不得 `import meapet.*`）的一致性由参数化测试钉住。

这条对提案的结论有影响：D4 的真实形状不是"查表失败后静默回落"，而是"查表**不存在**"。

---

## 7. 验收与读数（可复跑）

```bash
# 新增的针对性测试
python -m pytest -q tests/test_vits_route_and_knobs.py     # 57 passed

# CONTRIBUTING 的三条闸门
python -m pytest -q                                        # 957 passed / 50 failed / 9 skipped
python -m ruff check meapet wizard scripts tests           # All checks passed!
python -m compileall -q meapet wizard                      # exit 0
```

- **50 个失败与基线逐条同批**，全是环境门槛（websockets 未装 / Athena 状态根缺失 /
  Wayland 专用），非本次改动引入，本仓库判回归时不重查。
- 测试文件钉死四件事：判据九格全表逐格断言；`force_inprocess` 一旦重新变成惰性即红；
  两条路旋钮取值逐字相同且都进 argv；两份 `resolve_speaker`（含 `HParams` 形状、
  并对随包真模型配置）解析结果相同。
- 提案说 Linux 侧全量 984 passed、Windows 侧 957 passed —— 两个数字差在不同环境的
  收集/跳过集，不是回归信号。

---

## 8. 提案约束的遵守情况

| 约束 | 状态 |
|---|---|
| 不改远端历史 | ✅ 遵守 |
| 不 push | ✅ Linux 侧遵守；本次推送由维护者（本仓库 owner）执行，见提交 `b6c7ae0` 已在 `origin/main` |
| 不引入新外联 | ✅ 无新增网络调用 |
| 就绪判据单一来源是交付脚本 | ⚠️ **未成立**，见 §5 |
| 探针税不落在 `speak()` 路径上 | ✅ 遵守：说话人校验只在 `MeaTTS.__init__` 读一次 2.5 KB JSON，**不加载模型**；`speak()` 只多一行路由日志 |

---

## 9. 待办 / 留给维护者与提案人的两条

1. **W4**：补 `vits_infer.py --check-deps`（含三档结论）并取 Windows 探针耗时读数，
   或明确它归 Linux 侧那份交付件、Windows 侧不需要。见 §5。
2. **`vits_speaker` 等三个新键是否在向导里开输入框**：现在它们只能手改 `config.json`
   （`config.example.json` 已给出示例值）。D4 的"用户可配"因此是**通路打开了、UI 还没开**。
   这属于产品取舍，不在本件授权面内，故未擅自加控件。

---

## 10. 相关文档

- [`docs/tts-vits-routing.zh-CN.md`](tts-vits-routing.zh-CN.md) —— 技术文档：判据表、配置键、
  日志怎么读、实测读数、验收命令。**这是唯一的判据说明来源。**
- [`docs/troubleshooting.zh-CN.md`](troubleshooting.zh-CN.md) —— 用户视角：
  「VITS 填了解释器却不走子进程 / 填了 `vits_inprocess: true` 却没生效」。
- [`docs/backend-and-control.md`](backend-and-control.md) 第 6 节 —— 指向上面那份。
- `config.example.json` —— 5 个 `vits_*` 键的示例值。
