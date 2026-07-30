# MeaPet 视觉重设计规范 — 墨樱夜 / Ink-Sakura Night

> 本文取代 `MASTER.md` 中的**视觉决策**（色值、材质、层次语言）。
> `MASTER.md` 的**可访问性与交互规则**、`pages/desktop.md`、`pages/wizard.md` 的交互契约**全部继续生效，不得违反**。
> 本次只改视觉：不动任何 `objectName`、控件层级、布局逻辑、配置键与行为。

---

## 1. 视觉方向

### 1.1 方向陈述

**墨樱夜（Ink-Sakura Night）。**

夜色不是灰蓝的，是墨紫的。把底色从冷灰蓝换成带红调的墨紫，浅粉白的角色站在任何界面前都显暖、显亮。层次不再靠"深灰底 + 一圈灰边"平铺堆叠，而靠一束自上而下的月光：每个面用竖向渐变把顶端提亮一档、只让上沿的边最亮——不用阴影也能读出厚度。粉色不再当大面积按钮底色，只留三处：一个主行动、一根腰线、一圈气泡描边；橙杏只出现在渐变末端。界面收着，光留给她。

（198 字）

### 1.2 为什么适合这个产品

| 产品事实 | 设计回应 |
|---|---|
| 角色是浅粉白 + 白外套粉内衬 | 底色转为**暖调墨紫**（红分量高于蓝调灰）。冷灰蓝会把粉白压成"发灰"，暖墨紫让她像被灯照着。 |
| 桌宠常年叠在**不可控壁纸**上 | 每个浮层都是"不透明填充 + 双环（外墨环 / 内亮环）"。亮壁纸靠外墨环压边，暗壁纸靠内亮环提亮，两种壁纸都不糊。 |
| PyQt5 无 `box-shadow` | 深度语言改为**月光缝**（竖向渐变 + 更亮的 `border-top-color`）。这是纯 QSS 能做的、最接近真实材质的厚度线索。 |
| 当前"每张卡都一样" | 用**三级材质**区分：凹陷井（输入）/ 平面纸（卡片）/ 抬起层（浮层、二级卡）。同色不同材质，而不是同材质不同灰度。 |
| 当前粉色到处都是 | 粉色配额化：**一屏一个主按钮 + 腰线 + 气泡描边**。稀缺才有指向性，也不会压过角色。 |
| 陪伴向、二次元 | 圆角整体加大（8/12/18 → 10/14/20），菜单选中用粉→紫罗兰扫光，向导顶部有一枚樱色辉光。有情绪，但不吵。 |

### 1.3 三个结构装置（全站复用，是"记忆点"）

**装置 A — 月光缝（Moonlight Seam）**
所有卡片 / 面板 / 浮层的背景一律是竖向线性渐变，顶端亮一档、底端沉一档；同时 `border-top-color` 用紫罗兰半透明，比其余三边亮。

```qss
background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
    stop:0 <seam>, stop:0.45 <base>, stop:1 <base_deep>);
border: 1px solid <border>;
border-top-color: rgba(205, 184, 255, 90);
```

`rgba(205,184,255,90)` 叠在 surface 上合成 `#5E5278`，与卡面对比 **2.35:1** — 装饰性高光的正确强度（不是信息，不需要 3:1）。

**装置 B — 樱腰线（Sakura Rule）**
嵌套一级的容器（SectionCard / TurnCard / StatusCard）左边一根 3px 樱色竖线：`border-left: 3px solid rgba(255, 157, 190, 110);`
用途：标记"这是父卡里的一个小节"，替代现在"再画一个一模一样的灰框"。

> 兼容退路：若某平台在 `border-radius` + 非对称边宽下渲染出现缺角，改用背景渐变造腰线：
> `background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #FF9DBE, stop:0.010 #FF9DBE, stop:0.0101 #2E2440, stop:1 #2E2440);`（此时放弃该卡的竖向月光缝）。

**装置 C — 双环浮层（Double-Ring Float）**
一切叠在桌面壁纸上的表面（气泡、聊天输入、状态面板、启动页、同意弹窗、菜单）必须有：
1. 不透明或接近不透明的填充（alpha ≥ 240）；
2. **外墨环** `#0B0713`（自绘）或 `QGraphicsDropShadowEffect(rgba(6,4,12,·))`（控件）；
3. **内亮环** `border: 1px solid #7C69A0`（3.49:1 vs surface，满足 UI 图形 ≥3:1）。

---

## 2. 完整令牌表

### 2.1 PALETTE 新值（键名一字不改，仅换值）

```python
_PALETTE = {
    "canvas": "#16111F",
    "surface": "#221A2E",
    "surface_elevated": "#2E2440",
    "surface_input": "#100C18",
    "primary": "#FF9DBE",
    "primary_hover": "#FFB6CE",
    "on_primary": "#2B0F1C",
    "secondary": "#FFC48F",
    "accent": "#B7A6FF",
    "text_primary": "#FAF6FB",
    "text_secondary": "#D6CBE0",
    "text_muted": "#A79BB8",
    "border": "#46385C",
    "border_strong": "#7C69A0",
    "focus": "#CDB8FF",
    "success": "#6FE0B4",
    "warning": "#FFD37A",
    "danger": "#FF8FA0",
    "on_danger": "#2C0E15",
}
```

| 键 | 旧值 | 新值 | 语义与用法 |
|---|---|---|---|
| `canvas` | `#0E1020` | **`#16111F`** | 墨紫夜底。对话框 / 向导外壳底、拖选层遮罩色 |
| `surface` | `#17192D` | **`#221A2E`** | 主卡片"纸面" |
| `surface_elevated` | `#20233D` | **`#2E2440`** | 抬起层：浮层、二级卡、下拉列表、次级按钮面 |
| `surface_input` | `#111326` | **`#100C18`** | **凹陷井**（比 canvas 更深，输入区读作"挖下去"） |
| `primary` | `#FF91B4` | **`#FF9DBE`** | 樱粉。一屏一处主行动 + 腰线 + 气泡内环 |
| `primary_hover` | `#FFA8C4` | **`#FFB6CE`** | 主行动 hover |
| `on_primary` | `#26131B` | **`#2B0F1C`** | 粉底上的文字（也用于文本选中前景） |
| `secondary` | `#FFB36B` | **`#FFC48F`** | 杏橙。**只作渐变终点**，不单独当底色 |
| `accent` | `#A69BFF` | **`#B7A6FF`** | 紫藤。eyebrow、幽灵按钮 hover 底、次级强调 |
| `text_primary` | `#F8F8FC` | **`#FAF6FB`** | 标题正文（微微偏粉的白） |
| `text_secondary` | `#CACCE0` | **`#D6CBE0`** | 说明、字段标签 |
| `text_muted` | `#9FA3BC` | **`#A79BB8`** | 辅助提示（仍满足 AA） |
| `border` | `#3B3E5B` | **`#46385C`** | **静态**分隔：卡边、分割线、小节框 |
| `border_strong` | `#555A7B` | **`#7C69A0`** | **交互控件**边：输入框、按钮、菜单外沿（≥3:1） |
| `focus` | `#C0B9FF` | **`#CDB8FF`** | 2px 焦点环、月光缝高光源色 |
| `success` | `#70DDB0` | **`#6FE0B4`** | 成功（永远配文字） |
| `warning` | `#F4CC75` | **`#FFD37A`** | 警告 / 倒计时 / 隐私 eyebrow |
| `danger` | `#FF8892` | **`#FF8FA0`** | 错误、危险操作、红点 |
| `on_danger` | `#2A1014` | **`#2C0E15`** | 危险实底上的文字 |

**关键结构性改动（不只是换色）：**
1. `surface_input` 现在比 `canvas` **更深**（L=0.00439 < 0.00670）→ 输入区从"另一块灰"变成"凹槽"，这是层次感的主要来源。
2. `border` 与 `border_strong` 拆成两种职责（静态 vs 交互），不再混用。`border_strong` 提到 **3.49:1**，满足 `MASTER.md` "UI 图形至少 3:1"（旧值 `#555A7B` 对 `#17192D` 仅 **2.31:1**，是既有缺陷）。
3. `secondary` 降级为纯渐变终点，杜绝"橙色色块"。

### 2.2 对比度自证

WCAG 2.x 相对亮度：对每个通道 `c = C/255`，
`c_lin = c/12.92 (c ≤ 0.04045)` 否则 `((c+0.055)/1.055)^2.4`；
`L = 0.2126·R_lin + 0.7152·G_lin + 0.0722·B_lin`；
`ratio = (L_lighter + 0.05) / (L_darker + 0.05)`。

**逐色相对亮度：**

| 键 | Hex | L |
|---|---|---|
| canvas | `#16111F` | 0.00670 |
| surface | `#221A2E` | 0.01276 |
| surface_elevated | `#2E2440` | 0.02213 |
| surface_input | `#100C18` | 0.00439 |
| primary | `#FF9DBE` | 0.49092 |
| primary_hover | `#FFB6CE` | 0.59172 |
| on_primary | `#2B0F1C` | 0.00939 |
| secondary | `#FFC48F` | 0.62723 |
| accent | `#B7A6FF` | 0.44560 |
| text_primary | `#FAF6FB` | 0.93201 |
| text_secondary | `#D6CBE0` | 0.62390 |
| text_muted | `#A79BB8` | 0.35119 |
| border | `#46385C` | 0.04903 |
| border_strong | `#7C69A0` | 0.16926 |
| focus | `#CDB8FF` | 0.54480 |
| success | `#6FE0B4` | 0.59986 |
| warning | `#FFD37A` | 0.69254 |
| danger | `#FF8FA0` | 0.43443 |
| on_danger | `#2C0E15` | 0.00904 |

**单测强制的 6 组（`tests/test_ui_refactor.py::test_semantic_palette_meets_text_contrast_targets`）：**

| # | 前景 / 背景 | 计算 | 结果 | 要求 | 判定 |
|---|---|---|---|---|---|
| 1 | text_primary / surface | (0.93201+0.05)/(0.01276+0.05) | **15.65** | ≥4.5 | PASS |
| 2 | text_secondary / surface | (0.62390+0.05)/(0.01276+0.05) | **10.74** | ≥4.5 | PASS |
| 3 | text_muted / canvas | (0.35119+0.05)/(0.00670+0.05) | **7.08** | ≥4.5 | PASS |
| 4 | on_primary / primary | (0.49092+0.05)/(0.00939+0.05) | **9.11** | ≥4.5 | PASS |
| 5 | success / surface | (0.59986+0.05)/(0.01276+0.05) | **10.35** | ≥4.5 | PASS |
| 6 | danger / surface | (0.43443+0.05)/(0.01276+0.05) | **7.72** | ≥4.5 | PASS |

样例演算（第 4 组）：`primary #FF9DBE` → R=255/255=1.0 → 1.0；G=157/255=0.61569 → ((0.61569+0.055)/1.055)^2.4 = 0.33759；B=190/255=0.74510 → ((0.74510+0.055)/1.055)^2.4 = 0.55201。
`L = 0.2126×1.0 + 0.7152×0.33759 + 0.0722×0.55201 = 0.2126 + 0.24145 + 0.03986 = 0.49092`。
`on_primary #2B0F1C` → L = 0.00939。`ratio = 0.54092 / 0.05939 = 9.11` ✓

**附加保证（非强制，但本规范承诺）：**

| 组合 | 比值 | 目标 |
|---|---|---|
| text_primary / canvas · elevated · input | 17.32 · 13.61 · 18.05 | ≥4.5 ✓ |
| text_secondary / canvas · elevated · input | 11.88 · 9.34 · 12.39 | ≥4.5 ✓ |
| text_muted / surface · elevated · input | 6.39 · 5.56 · 7.38 | ≥4.5 ✓ |
| on_primary / primary_hover | 10.81 | ≥4.5 ✓ |
| on_primary / secondary（渐变末端） | 11.40 | ≥4.5 ✓ |
| on_danger / danger | 8.21 | ≥4.5 ✓ |
| primary / surface · canvas · elevated | 8.62 · 9.54 · 7.50 | ≥4.5 ✓ |
| accent / surface · canvas | 7.90 · 8.74 | ≥4.5 ✓ |
| warning / surface · canvas | 11.83 · 13.09 | ≥4.5 ✓ |
| success / elevated；danger / elevated | 9.01 · 6.72 | ≥4.5 ✓ |
| **focus / surface · canvas · input · elevated** | 9.48 · 10.49 · 10.94 · 8.25 | ≥3.0 ✓ |
| **border_strong / surface · input · elevated · canvas** | 3.49 · 4.03 · 3.04 · 3.87 | ≥3.0 ✓ |
| border / surface · canvas（静态分隔，装饰级） | 1.58 · 1.75 | — |

**合成态自证（叠加半透明后的实际像素）：**

| 状态 | 合成色 | 与其上文字 | 判定 |
|---|---|---|---|
| 输入框选中底 `rgba(255,157,190,200)` on `#100C18` | `#CB7E9A` | `on_primary` = **5.88** | ✓ |
| 下拉项选中 `rgba(255,157,190,70)` on `#2E2440` | `#674563` | `text_primary` = **7.53** | ✓ |
| 菜单项选中 `rgba(255,157,190,52)` on `#221A2E` | `#4F354B` | `text_primary` = **10.11** | ✓ |
| 幽灵按钮 hover `rgba(183,166,255,26)` on `#221A2E` | `#312843` | `text_primary` = **12.96** | ✓ |
| 危险 hover `rgba(255,143,160,40)` on `#221A2E` | `#452C40` | `danger` = **5.74** | ✓ |
| 校验条 `rgba(255,143,160,26)` on `#221A2E` | `#39263A` | `danger` = **6.41** | ✓ |
| 倒计时条 `rgba(255,211,122,24)` on `#221A2E` | `#372B35` | `warning` = **9.52** | ✓ |
| 禁用态 `rgba(167,155,184,120)` on `#241C32` | `#625871` | **2.45**（刻意低，表达不可交互） | 符合预期 |

### 2.3 渐变定义（全站只有这 6 条，编号复用）

| 编号 | 名称 | 定义 | 用在哪 |
|---|---|---|---|
| **G1** | 樱杏主行动 | `qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #FF9DBE, stop:1 #FFC48F)` | PrimaryButton / SendButton / AllowUploadButton / BrandMark / SplashMark / 勾选态指示器 |
| **G1h** | 主行动 hover | `qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #FFB6CE, stop:1 #FFD3AC)` | 上述 `:hover` |
| **G2** | 进度 / 滑轨 | `qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #FF9DBE, stop:0.55 #FFB6CE, stop:1 #FFC48F)` | QProgressBar::chunk、QSlider::sub-page |
| **G3** | 月光缝（纸面） | `qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #2A2139, stop:0.45 #221A2E, stop:1 #1F1829)` | PageCard / SizeDialogCard / TimelineCard / ChatComposer / SplashCard / Tab pane |
| **G4** | 月光缝（抬起层） | `qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #3D3154, stop:0.45 #2E2440, stop:1 #281F38)` | SectionCard / StatusCard / TurnCard / 次级按钮 / QMenu |
| **G5** | 樱色扫光（选中） | `qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(255,157,190,52), stop:1 rgba(183,166,255,30))` | QMenu::item:selected、QTabBar::tab:hover |
| **G6** | 向导顶部辉光 | `qradialgradient(cx:0.08, cy:0.0, radius:0.9, fx:0.08, fy:0.0, stop:0 rgba(255,157,190,30), stop:0.5 rgba(183,166,255,14), stop:1 rgba(22,17,31,0))` | WizardHeader |

> 方向约定：**主行动一律 135°（左上→右下）**，**材质一律 90°（上→下）**，**进度一律 0°（左→右）**。不允许出现第四种方向。

### 2.4 圆角、间距、字阶

**圆角（`ui_theme.py` 改这三个常量）：**

```python
RADIUS_SMALL = 10    # 旧 8  — 控件：输入、按钮、小节卡、chip
RADIUS_MEDIUM = 14   # 旧 12 — 卡片：PageCard、Composer、TabPane、StatusCard
RADIUS_LARGE = 20    # 旧 18 — 外壳：WizardShell、SplashCard、CloudConsentCard
```

附加固定值（写死在各自 QSS）：菜单 `12`、菜单项 `8`、pill/chip `11`、复选框指示器 `6`、单选钮 `11`、进度条 `8`（chunk `7`）、滚动条把手 `4`、气泡 `DIALOGUE_RADIUS = 22`。

**间距：不变**（`4 / 8 / 12 / 16 / 20 / 24 / 32`）。弹窗使用设计基准尺寸，但不得用固定几何压住字体缩放后的布局；统一通过 `resize_dialog_to_content()` 按 `sizeHint` 和屏幕可用区域收敛。

**字阶（收敛为 8 档，去掉随手写的中间值）：**

| 档 | px | 用途 |
|---|---|---|
| micro | 11 | eyebrow（全大写拉丁）、hint、detail |
| small | 12 | helper text、meta、pill |
| body-s | 13 | 说明正文、字段标签、quote |
| body | 14 | 界面默认、菜单项、输入框、复选框 |
| lead | 15 | 气泡正文、主按钮文字 |
| title-s | 16 | SectionTitle、BrandName |
| title-m | 20 / 22 | 对话框标题 20 / 页标题 22 / 面板标题 22 |
| display | 26 | SplashTitle |

**字重：只允许 400 / 600 / 700。**
理由：项目只打包了 `LXGWWenKai-Regular.ttf`，`650` `750` `800` 全部合成为同一种粗体，只是徒增 QSS 噪声。全局把 `650→600`、`750/800→700`、`500→400`。

**字族：不变。** 正文与展示统一 `FONT_FAMILY`（= `"LXGW WenKai"`），等宽用 `MONO_FONT_FAMILY`。展示位（标题、品牌、主按钮）继续显式写 `font-family: {DISPLAY_FONT_FAMILY};`（单测检查其存在于 `CHAT_COMPOSER_STYLE` 与 `DIALOGUE_STYLE`）。

> Qt5 QSS **不支持 `letter-spacing`**。eyebrow 的字距若要撑开，只能在代码里
> `f = label.font(); f.setLetterSpacing(QFont.AbsoluteSpacing, 1.2); label.setFont(f)`。列为可选项。

### 2.5 QGraphicsDropShadowEffect 总表

Qt 把子控件的阴影裁在父控件矩形内，所以必须满足 **`外层 margin ≥ blurRadius/2 + |yOffset|`**。下表已按各表面**现有的** `outer.setContentsMargins` 取值，不改布局即可安全落地。

| 表面 | 施加对象 | 现有外边距 | color | blurRadius | offset (x, y) |
|---|---|---|---|---|---|
| 启动页 | `StartupSplash.card` | 12 | `rgba(6, 4, 12, 200)` | **18** | (0, 3) |
| 向导 | `SetupWizard.container` | 16 | `rgba(6, 4, 12, 205)` | **24** | (0, 4) |
| 大小调节 | `SizeScaleDialog` 的 `SizeDialogCard` | 10 | `rgba(6, 4, 12, 195)` | **14** | (0, 3) |
| 云端同意 | `CloudConsentDialog` 的 `CloudConsentCard` | 6 | `rgba(6, 4, 12, 200)` | **8** | (0, 2) |
| 截图范围同意 | `CaptureScopeConsentDialog` 的 card | 6 | `rgba(6, 4, 12, 200)` | **8** | (0, 2) |
| 状态面板 | 三个 `StatusCard` 各一个 | 卡间距 12 | `rgba(6, 4, 12, 120)` | **16** | (0, 4) |
| 聊天输入 | — **不加** | 0 | 见下方说明 | — | — |
| 气泡 | — **不加** | — | 自绘阴影，见 §3.1 | — | — |

**为什么聊天输入不加：** `ChatInputBox` 的 `outer.setContentsMargins(0,0,0,0)` 且窗口 `setFixedSize(480,112)`，没有留给阴影的像素，加了必被裁成硬边。本次靠**装置 C 双环**保证浮起感。
**可选后续（属于布局改动，本次不做）：** 把 `outer.setContentsMargins(0,0,0,0)` 改为 `(14, 10, 14, 16)`，即可加 `rgba(6,4,12,200) / blur 26 / (0, 6)`；单测 `assertLessEqual(composer.width(), 480)` 仍通过。同理，把 splash 外边距 12→20、同意框 6→14，可把 blur 提到 30 / 20，观感更好。

**气泡不能用 DropShadow 的硬约束：** `DialogueBox._container` 已挂了 `QGraphicsOpacityEffect`（淡入淡出依赖它），**一个 QWidget 只能挂一个 QGraphicsEffect**，所以气泡的阴影必须在 `paintEvent` 里手绘。

---

## 3. 逐表面规范

### ① 桌宠回复气泡（自绘，`meapet/desktop/widgets.py` + `DIALOGUE_STYLE`）

用户可见频率最高的表面。目标：**在纯白壁纸和纯黑壁纸上都一眼可读**。

#### 3.1 `SpeechBubbleFrame.paintEvent` 绘制顺序

```
1) 墨影三叠（伪模糊）
2) 外墨环 + 渐变填充（同一次 drawPath，pen 外溢一半形成外环）
3) 内樱环（NoBrush，画在填充之上）
```

**参数：**

| 项 | 值 |
|---|---|
| 墨影 pass 1 | `QColor(6, 4, 12, 46)`，`path.translated(0, 4)` |
| 墨影 pass 2 | `QColor(6, 4, 12, 58)`，`path.translated(0, 3)` |
| 墨影 pass 3 | `QColor(6, 4, 12, 74)`，`path.translated(0, 2)` |
| 填充渐变 | `QLinearGradient(body.topLeft(), body.bottomLeft())`（**竖向**，不再是对角）<br>`stop 0.00 → #2E2440`<br>`stop 0.42 → #251C33`<br>`stop 1.00 → #1A1426` |
| 外墨环 | `QPen(QColor("#0B0713"), 2.6)`，与填充同一次 `drawPath` |
| 内樱环 | `QPen(QColor(mood), 1.6)`，`alpha = 235`，`setBrush(Qt.NoBrush)`，第二次 `drawPath` |
| 抗锯齿 | `QPainter.Antialiasing = True`（不变） |

**为什么双环有效：** 白壁纸上 `#0B0713` 外环提供 ~19:1 的边界；黑壁纸上内樱环（`primary` 对 `#0B0713` 极高对比）提供边界。任一壁纸都至少有一环在工作。

正文最深处 `#1A1426` 上 `text_primary` = **16.76:1**，最浅处 `#2A2038` 上 = **14.42:1**。

#### 3.2 心情描边色（`MOOD_BORDER_COLORS` 同步换新）

情绪**只**改内环色，仍必须配合角色表情/文案，不作为唯一语义（沿用 `desktop.md`）。

```python
MOOD_BORDER_COLORS = {
    "happy":      "#FFC48F",   # secondary
    "annoyed":    "#FF8FA0",   # danger
    "sad":        "#8FA8DE",
    "shy":        "#FF9DBE",   # primary
    "curious":    "#B7A6FF",   # accent
    "surprised":  "#FFD37A",   # warning
    "melancholy": "#A79BB8",   # text_muted
    "talking":    "#FF9DBE",
    "neutral":    "#FF9DBE",
}
```

#### 3.3 尾巴形状建议

保持现有底部"斜向双贝塞尔扫向角色一侧"的彗尾造型（这是本产品已有的、少见的好细节），只做两处收紧：

| 常量 | 旧 | 新 | 影响 |
|---|---|---|---|
| `DIALOGUE_RADIUS` | 20 | **22** | 更圆更软；只参与 `_clamped_anchor` 的 inset，**不参与尺寸计算**，安全 |
| `DIALOGUE_TAIL_BASE` | 28 | **24** | 尾根更收，彗尾更利落；只在 `_tail_path` / `_clamped_anchor` 使用，**不参与尺寸计算**，安全 |
| `_body_rect()` 的 `edge` | 1.5 | **2.0** | 给 2.6px 外环留出外溢空间，避免被窗口边裁掉 |

**绝对不要改** `DIALOGUE_TAIL_SIZE / _DEPTH / _REACH / MIN_WIDTH / MAX_WIDTH / MAX_HEIGHT / _HORIZONTAL_PADDING / _VERTICAL_PADDING` —— 它们进入 `show_text()` 的尺寸算式，单测断言短气泡 `< 260 × 130`、长气泡 `≤ MAX`。

#### 3.4 `DIALOGUE_STYLE`（QSS 部分）

```qss
QFrame#DialogueBubble {
    background: transparent;              /* 自绘，勿加背景 */
    border: none;
}
QLabel#DialogueText {
    background: transparent;
    color: #FAF6FB;
    border: none;
    padding: 0;
    font-family: "LXGW WenKai";           /* 必须保留：单测检查 DISPLAY_FONT_FAMILY */
    font-size: 15px;
    font-weight: 400;                     /* 旧 500，LXGW 无 Medium，500 等同 400 */
}
QScrollArea#DialogueScroll,
QScrollArea#DialogueScroll > QWidget > QWidget {
    background: transparent;
    border: none;
}
QScrollArea#DialogueScroll QScrollBar:vertical {
    width: 8px; margin: 5px 3px; background: transparent;
}
QScrollArea#DialogueScroll QScrollBar::handle:vertical {
    min-height: 28px; border-radius: 4px;
    background: rgba(255, 157, 190, 165);
}
QScrollArea#DialogueScroll QScrollBar::handle:vertical:hover {
    background: rgba(255, 182, 206, 210);
}
QScrollArea#DialogueScroll QScrollBar::add-line:vertical,
QScrollArea#DialogueScroll QScrollBar::sub-line:vertical { height: 0; }
```

> 单测约束：`DIALOGUE_STYLE` 必须包含 `QFrame#DialogueBubble`，且**不得**出现 `DialogueName` / `DialogueAccent`。

#### 3.5 气泡栈透明度

`DIALOGUE_STACK_OPACITIES = (0.52, 0.76, 1.0)` **保持不变**（`desktop.md` 契约：旧条更透）。新配色下 0.52 的旧气泡仍有 `#FAF6FB` 正文，实测足够辨识。

---

### ② 聊天输入面板（`CHAT_COMPOSER_STYLE`）

```qss
QWidget#ChatComposerRoot {
    color: #FAF6FB;
    font-family: "LXGW WenKai";
    background: transparent;
}
QFrame#ChatComposer {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(42, 33, 57, 252),
        stop:0.45 rgba(34, 26, 46, 251),
        stop:1 rgba(31, 24, 41, 251));
    border: 1px solid #7C69A0;
    border-top-color: rgba(205, 184, 255, 100);
    border-radius: 14px;
}
QLabel { background: transparent; border: none; }

QLabel#ComposerTitle {
    color: #FAF6FB;                       /* ← 必须是本规则的第一条声明（单测断言） */
    font-family: "LXGW WenKai";
    font-size: 13px;
    font-weight: 700;
}
QLabel#ComposerHint    { color: #A79BB8; font-size: 11px; }
QLabel#ComposerFeedback{ color: #FF8FA0; font-size: 11px; font-weight: 600; }

QLineEdit {
    min-height: 44px;
    background: #100C18;                  /* 凹陷井 */
    color: #FAF6FB;
    border: 1px solid #7C69A0;
    border-radius: 10px;
    padding: 0 14px;
    font-size: 14px;
    selection-background-color: rgba(255, 157, 190, 200);
    selection-color: #2B0F1C;
}
QLineEdit:hover  { border-color: #CDB8FF; }
QLineEdit:focus  { border: 2px solid #CDB8FF; padding: 0 13px; }

QPushButton {                             /* 次级 / 幽灵基座 */
    min-height: 44px; min-width: 44px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #33284A, stop:1 #281F38);
    color: #FAF6FB;
    border: 1px solid #7C69A0;
    border-radius: 10px;
    padding: 0 14px;
    font-weight: 600;
}
QPushButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #3D3054, stop:1 #302543);
    border-color: #CDB8FF;
}
QPushButton:pressed { background: #241C32; }
QPushButton:focus   { border: 2px solid #CDB8FF; }

QPushButton#SendButton {                  /* 唯一主行动 */
    min-width: 80px;
    font-family: "LXGW WenKai";
    font-size: 15px;
    font-weight: 700;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #FF9DBE, stop:1 #FFC48F);
    color: #2B0F1C;
    border: 1px solid #FF9DBE;
}
QPushButton#SendButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #FFB6CE, stop:1 #FFD3AC);
    border-color: #FFB6CE;
}
QPushButton#SendButton:pressed  { background: #FF9DBE; }
QPushButton#SendButton:disabled {
    background: rgba(46, 36, 64, 150);
    color: rgba(167, 155, 184, 120);
    border-color: rgba(70, 56, 92, 150);
}

QPushButton#ComposerCloseButton {
    background: transparent;
    color: #A79BB8;
    border-color: transparent;
    padding: 0;
    font-size: 18px;
}
QPushButton#ComposerCloseButton:hover {
    background: rgba(255, 143, 160, 40);
    color: #FF8FA0;
    border-color: rgba(255, 143, 160, 110);
}
```

**占位符文字：** Qt5 QSS 无 `::placeholder`。若要让 placeholder 用 `text_muted`，在 `chat_input.py` 里：
```python
pal = self.input.palette()
pal.setColor(QPalette.PlaceholderText, QColor("#A79BB8"))
self.input.setPalette(pal)
```
（Qt ≥ 5.12）。列为可选增强。

---

### ③ 右键菜单 / 托盘菜单（`MENU_STYLE`）

菜单是"扫光"装置的主场：hover 从灰色水洗变成**粉→紫罗兰横扫**。

```qss
QMenu {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(61, 49, 84, 252),
        stop:0.5 rgba(46, 36, 64, 252),
        stop:1 rgba(34, 26, 46, 252));
    color: #FAF6FB;
    border: 1px solid #7C69A0;
    border-top-color: rgba(205, 184, 255, 110);
    border-radius: 12px;
    padding: 6px;
    font-family: "LXGW WenKai";
    font-size: 14px;                      /* 单测：menu.font().pixelSize() >= 14 */
}
QMenu::item {
    min-height: 34px;                     /* ≥32 契约；实测行高 ≥38（单测） */
    padding: 8px 28px 8px 14px;
    border: 1px solid transparent;
    border-radius: 8px;
    margin: 2px 4px;
}
QMenu::item:selected {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 rgba(255, 157, 190, 52),
        stop:1 rgba(183, 166, 255, 30));
    border-color: rgba(255, 157, 190, 120);
    color: #FAF6FB;
}
QMenu::item:pressed  { background: rgba(255, 157, 190, 78); }
QMenu::item:disabled { color: rgba(167, 155, 184, 130); }
QMenu::separator {
    height: 1px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 rgba(255, 157, 190, 90),
        stop:0.35 rgba(124, 105, 160, 110),
        stop:1 rgba(124, 105, 160, 0));
    margin: 5px 12px;
}
QMenu::indicator { width: 14px; height: 14px; left: 8px; }
QMenu::icon      { left: 10px; }
```

**行高验算：** `min-height 34` + 上下 `padding 8+8` 与 `border 1+1` 由 Qt 取较大者，实测行高 ≥ 38，满足单测 `actionGeometry(action).height() >= 38`。

**关于 `DangerAction`：** `_build_context_menu()` 给"重置所有记忆"的 **QAction** 设了 `setObjectName("DangerAction")`，但 **QAction 的 objectName 不参与 QSS 选择器**（QSS 只作用于 QWidget），这条样式今天就没有生效。
按 `MASTER.md` "状态不能只靠颜色表达"，正确做法不是补一条红色 QSS，而是**保留现有的 `standard_icon("reset")` 图标 + 文案 + 分隔线上方分组**作为危险信号。本规范**不为菜单项引入纯色差异化**。（`objectName` 保留不动，不影响任何测试。）

---

### ④ 状态面板（`STATUS_PANEL_STYLE` + `status_panel.py` 的 `paintEvent`）

现状问题：整块矩形照片 + 一层平铺黑 70，边缘是硬直角，压在桌面上像贴纸。

#### 4.1 `paintEvent` 重绘（仅绘制，不改布局）

```python
def paintEvent(self, event):
    painter = QPainter(self)
    painter.setRenderHint(QPainter.Antialiasing)

    radius = 20
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
    painter.setClipPath(clip)                       # ① 圆角裁切，去掉硬直角

    if self.bg_pix:
        painter.drawPixmap(0, 0, self.bg_pix)
        veil = QLinearGradient(0, 0, 0, self.height())   # ② 墨纱替代平铺黑
        veil.setColorAt(0.00, QColor(22, 17, 31, 150))
        veil.setColorAt(0.45, QColor(22, 17, 31, 205))
        veil.setColorAt(1.00, QColor(16, 12, 24, 238))
        painter.fillRect(self.rect(), veil)
    else:
        painter.fillRect(self.rect(), QColor(22, 17, 31, 238))

    painter.setClipping(False)                      # ③ 内亮环（双环装置）
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(QColor("#7C69A0"), 1))
    painter.drawPath(clip)
```

#### 4.2 QSS

```qss
QWidget#StatusPanelRoot {
    color: #FAF6FB;
    font-family: "LXGW WenKai";
    background: transparent;
}
QLabel { background: transparent; border: none; color: #FAF6FB; }

QLabel#PanelEyebrow { color: #B7A6FF; font-size: 11px; font-weight: 700; }
QLabel#PanelTitle   { color: #FAF6FB; font-family: "LXGW WenKai";
                      font-size: 22px; font-weight: 700; }
QLabel#TierLabel    { color: #FF9DBE; font-size: 18px; font-weight: 700; }  /* 樱色主角数值 */
QLabel#QuoteLabel   { color: #D6CBE0; font-size: 13px; font-style: italic; }
QLabel#StatsLabel   { color: #D6CBE0; font-size: 13px; }
QLabel#MemoryLabel  { color: #A79BB8; font-size: 12px; }
QLabel#PanelHint    { color: #A79BB8; font-size: 11px; }

QFrame#StatusCard {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(61, 49, 84, 222),
        stop:0.45 rgba(46, 36, 64, 218),
        stop:1 rgba(40, 31, 56, 218));
    border: 1px solid #46385C;
    border-top-color: rgba(205, 184, 255, 80);
    border-left: 3px solid rgba(255, 157, 190, 110);   /* 樱腰线 */
    border-radius: 14px;
}

QPushButton#PanelCloseButton {
    min-width: 64px; min-height: 44px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(51, 40, 74, 230), stop:1 rgba(40, 31, 56, 230));
    color: #FAF6FB;
    border: 1px solid #7C69A0;
    border-radius: 12px;
    font-weight: 600;
}
QPushButton#PanelCloseButton:hover {
    background: rgba(255, 143, 160, 45);
    color: #FF8FA0;
    border-color: rgba(255, 143, 160, 140);
}
QPushButton#PanelCloseButton:focus { border: 2px solid #CDB8FF; }

QProgressBar {
    min-height: 22px;
    background: #100C18;
    color: #FAF6FB;
    border: 1px solid #46385C;
    border-radius: 8px;
    text-align: center;
    font-size: 12px;
    font-weight: 700;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #FF9DBE, stop:0.55 #FFB6CE, stop:1 #FFC48F);
    border-radius: 7px;
}
```

**DropShadow：** 三个 `StatusCard` 各挂 `rgba(6,4,12,120) / blur 16 / (0, 4)`（卡间距 12 + 主边距 22，足够）。

---

### ⑤ 启动 Splash（`SPLASH_STYLE`）

```qss
QWidget#SplashRoot {
    color: #FAF6FB; font-family: "LXGW WenKai"; background: transparent;
}
QFrame#SplashCard {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #312647, stop:0.42 #221A2E, stop:1 #16111F);
    border: 1px solid #46385C;
    border-top-color: rgba(205, 184, 255, 95);
    border-radius: 20px;
}
QLabel { background: transparent; border: none; }

QLabel#SplashMark {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #FF9DBE, stop:1 #FFC48F);
    color: #2B0F1C;
    border-radius: 18px;                 /* 36×36 固定尺寸的正圆 */
    font-size: 18px;
    font-weight: 700;
}
QLabel#SplashTitle    { color: #FAF6FB; font-family: "LXGW WenKai";
                        font-size: 26px; font-weight: 700; }
QLabel#SplashSubtitle { color: #D6CBE0; font-size: 13px; }
QLabel#SplashStatus   { color: #FAF6FB; font-size: 14px; font-weight: 600; }
QLabel#SplashStatus[status="success"] { color: #6FE0B4; }
QLabel#SplashStatus[status="error"]   { color: #FF8FA0; }
QLabel#SplashDetail,
QLabel#SplashHint     { color: #A79BB8; font-size: 11px; }

QProgressBar {                            /* 代码里 setFixedHeight(8) */
    background: #100C18;
    border: 1px solid #46385C;
    border-radius: 5px;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #FF9DBE, stop:0.55 #FFB6CE, stop:1 #FFC48F);
    border-radius: 4px;
}
```

**DropShadow：** `splash.card` ← `rgba(6,4,12,200) / blur 18 / (0, 3)`。

---

### ⑥ 对话框族（`DIALOG_STYLE` + `CONSENT_DIALOG_STYLE`）

#### 6.1 `DIALOG_STYLE`（大小调节、时间线、本轮完整回复）

```qss
QDialog {
    background: #16111F; color: #FAF6FB;
    font-family: "LXGW WenKai"; font-size: 14px;
}

/* 一级纸面 */
QFrame#SizeDialogCard,
QFrame#TimelineCard {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #2A2139, stop:0.45 #221A2E, stop:1 #1F1829);
    border: 1px solid #46385C;
    border-top-color: rgba(205, 184, 255, 80);
    border-radius: 14px;
}
/* 二级：时间线里的每一轮，带樱腰线形成节奏 */
QFrame#TurnCard {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #3D3154, stop:0.45 #2E2440, stop:1 #281F38);
    border: 1px solid #46385C;
    border-left: 3px solid rgba(255, 157, 190, 110);
    border-radius: 12px;
}

QLabel { color: #FAF6FB; background: transparent; border: none; }
QLabel#PageTitle   { color: #FAF6FB; font-family: "LXGW WenKai";
                     font-size: 20px; font-weight: 700; }
QLabel#HelperText  { color: #A79BB8; font-size: 12px; }
QLabel#FieldLabel  { color: #D6CBE0; font-size: 12px; font-weight: 600; }
QLabel#TurnMeta    { color: #A79BB8; font-size: 12px; font-weight: 600; }
QLabel#TurnPreview { color: #FAF6FB; font-size: 13px; }
QLabel#TurnUser    { color: #D6CBE0; font-size: 12px; }
QLabel#ScaleValue  { color: #FF9DBE; font-size: 26px; font-weight: 700; }

QPlainTextEdit, QTextEdit {
    background: #100C18; color: #FAF6FB;
    border: 1px solid #7C69A0; border-radius: 10px;
    padding: 10px 12px; font-size: 13px;
    selection-background-color: rgba(255, 157, 190, 200);
    selection-color: #2B0F1C;
}
QPlainTextEdit:hover, QTextEdit:hover { border-color: #CDB8FF; }
QPlainTextEdit:focus, QTextEdit:focus { border: 2px solid #CDB8FF; padding: 9px 11px; }

QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 4px 2px; }
QScrollBar::handle:vertical {
    background: rgba(124, 105, 160, 170); border-radius: 4px; min-height: 28px;
}
QScrollBar::handle:vertical:hover { background: #A79BB8; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }

/* 次级按钮（默认） */
QPushButton {
    min-height: 44px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #33284A, stop:1 #281F38);
    color: #FAF6FB;
    border: 1px solid #7C69A0;
    border-radius: 10px;
    padding: 8px 18px;
    font-weight: 600;
}
QPushButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #3D3054, stop:1 #302543);
    border-color: #CDB8FF;
}
QPushButton:pressed  { background: #241C32; }
QPushButton:focus    { border: 2px solid #CDB8FF; padding: 7px 17px; }
QPushButton:disabled {
    background: rgba(46, 36, 64, 150);
    color: rgba(167, 155, 184, 120);
    border-color: rgba(70, 56, 92, 150);
}

/* 主行动 */
QPushButton#PrimaryButton {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #FF9DBE, stop:1 #FFC48F);
    color: #2B0F1C;
    border-color: #FF9DBE;
    font-weight: 700;
}
QPushButton#PrimaryButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #FFB6CE, stop:1 #FFD3AC);
    border-color: #FFB6CE;
}
QPushButton#PrimaryButton:pressed { background: #FF9DBE; }

/* 幽灵 */
QPushButton#GhostButton {
    background: transparent; border-color: transparent; color: #D6CBE0;
}
QPushButton#GhostButton:hover {
    background: rgba(183, 166, 255, 26);
    color: #FAF6FB;
    border-color: rgba(183, 166, 255, 60);
}
QPushButton#GhostButton:pressed { background: rgba(183, 166, 255, 44); }

QSlider::groove:horizontal {
    height: 6px; background: #100C18;
    border: 1px solid #46385C; border-radius: 3px;
}
QSlider::sub-page:horizontal {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #FF9DBE, stop:0.55 #FFB6CE, stop:1 #FFC48F);
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background: #FAF6FB; border: 3px solid #FF9DBE;
    width: 20px; margin: -8px 0; border-radius: 11px;
}
QSlider::handle:horizontal:hover   { border-color: #CDB8FF; }
QSlider::handle:horizontal:pressed { background: #FFB6CE; }
QSlider:focus { border: 1px solid #CDB8FF; border-radius: 5px; }
```

#### 6.2 `CONSENT_DIALOG_STYLE`（截图同意 / 云端同意）

> **尺寸红线（响应式修订）：** `CloudVisionConsentDialog` 以 `420×270` 为设计基准，`CaptureScopeConsentDialog` 以 `440px` 为基准宽度；二者都必须随 80%–150% 字体缩放增长，并夹在当前屏幕可用区域内。不得使用 `setFixedSize` / `setFixedWidth` 锁住布局。

```qss
QDialog#CloudConsentRoot,
QDialog#CaptureScopeConsentRoot {
    color: #FAF6FB; font-family: "LXGW WenKai"; background: transparent;
}
QFrame#CloudConsentCard {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #312647, stop:0.42 #221A2E, stop:1 #1B1526);
    border: 1px solid #7C69A0;
    border-top-color: rgba(205, 184, 255, 100);
    border-radius: 20px;
}
QFrame#SectionCard {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #352A4A, stop:1 #2A213A);
    border: 1px solid #46385C;
    border-left: 3px solid rgba(255, 157, 190, 100);
    border-radius: 10px;
}
QLabel { color: #FAF6FB; background: transparent; border: none; }
QLabel#ConsentEyebrow { color: #FFD37A; font-size: 11px; font-weight: 700; }
QLabel#ConsentTitle   { color: #FAF6FB; font-family: "LXGW WenKai";
                        font-size: 20px; font-weight: 700; }
QLabel#ConsentBody    { color: #D6CBE0; font-size: 13px; }
QLabel#FieldLabel     { color: #D6CBE0; font-size: 12px; font-weight: 600; }
QLabel#HelperText     { color: #A79BB8; font-size: 11px; }
QLabel#SelectionSummary {
    color: #D6CBE0; font-size: 12px;
    padding: 7px 9px;                     /* 不变 */
    background: #100C18;
    border: 1px solid #46385C;
    border-radius: 10px;
}
QLabel#ConsentValidation {
    color: #FF8FA0; font-size: 12px; font-weight: 600;
    padding: 5px 8px;                     /* 不变 */
    background: rgba(255, 143, 160, 26);
    border: 1px solid rgba(255, 143, 160, 90);
    border-radius: 10px;
}
QLabel#ConsentCountdown {
    color: #FFD37A; font-size: 12px; font-weight: 600;
    padding: 6px 10px;                    /* 不变 */
    background: rgba(255, 211, 122, 24);
    border: 1px solid rgba(255, 211, 122, 85);
    border-radius: 10px;
}

QComboBox {
    min-height: 42px;                     /* 不变 */
    color: #FAF6FB; background: #100C18;
    border: 1px solid #7C69A0; border-radius: 10px;
    padding: 0 12px;
    selection-background-color: rgba(255, 157, 190, 200);
    selection-color: #2B0F1C;
}
QComboBox:hover { border-color: #CDB8FF; }
QComboBox:focus { border: 2px solid #CDB8FF; }
QComboBox::drop-down {
    subcontrol-origin: padding; subcontrol-position: top right;
    width: 32px;                          /* 不变 */
    background: rgba(46, 36, 64, 200);
    border: none; border-left: 1px solid #7C69A0;
    border-top-right-radius: 9px; border-bottom-right-radius: 9px;
}
QComboBox::down-arrow { image: url("<BUNDLED_CHEVRON_DOWN_PATH>"); width: 10px; height: 7px; }
QComboBox QAbstractItemView {
    color: #FAF6FB; background: #2E2440;
    border: 1px solid #7C69A0; border-radius: 10px;
    selection-color: #FAF6FB;
    selection-background-color: rgba(255, 157, 190, 70);
    padding: 4px; outline: 0;
}

QPushButton {
    min-width: 112px; min-height: 44px;   /* 不变 */
    padding: 0 16px;                      /* 不变 */
    color: #FAF6FB;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #33284A, stop:1 #281F38);
    border: 1px solid #7C69A0; border-radius: 10px;
    font-weight: 600;
}
QPushButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #3D3054, stop:1 #302543);
    border-color: #CDB8FF;
}
QPushButton:focus { border: 2px solid #CDB8FF; }

QPushButton#SelectRegionButton,
QPushButton#RefreshWindowsButton {
    color: #FAF6FB;
    background: rgba(183, 166, 255, 26);
    border-color: #7C69A0;
}
QPushButton#SelectRegionButton:hover,
QPushButton#RefreshWindowsButton:hover {
    background: rgba(183, 166, 255, 52);
    border-color: #CDB8FF;
}
QPushButton#RefreshWindowsButton { min-width: 72px; padding-left: 10px; padding-right: 10px; }

QPushButton#AllowUploadButton {           /* 唯一主行动 */
    font-family: "LXGW WenKai"; font-size: 14px; font-weight: 700;
    color: #2B0F1C;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #FF9DBE, stop:1 #FFC48F);
    border-color: #FF9DBE;
}
QPushButton#AllowUploadButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #FFB6CE, stop:1 #FFD3AC);
    border-color: #FFB6CE;
}
QPushButton#CancelUploadButton:default {  /* 默认=安全选项，必须同样醒目 */
    color: #FAF6FB;
    border: 2px solid #CDB8FF;
    background: rgba(205, 184, 255, 30);
}
```

#### 6.3 拖选层 `ScreenRegionSelector.paintEvent`

| 项 | 旧 | 新 |
|---|---|---|
| 遮罩 | `PALETTE["canvas"]` α=178 | `PALETTE["canvas"]` **α=190**（新 canvas 更暖更深，压得住亮壁纸） |
| 选区边 | `focus` 2px | `focus` 2px **外**，再叠一圈 `primary` 1px（`selection.adjusted(3,3,-3,-3)`）→ 双环 |
| 说明胶囊底 | `surface` α=235，圆角 10 | `surface` **α=244**，圆角 **14** |
| 胶囊边 | `border_strong` 1px | `border_strong` 1px（新值自动变亮，无需改代码） |
| 胶囊文字 | `text_primary` | 不变 |

---

### ⑦ 配置向导 → 见 §4 专章

---

## 4. 向导专章（`wizard/styles.py` + `wizard/app.py`）

> 现实现是**顶部标签栏**（`QTabWidget#ConfigurationTabs`，六个固定标签），不是侧边导航。本次**不改导航形态**（`wizard.md` 的六标签契约 + 单测依赖）。下文 4.2 同时给出"顶部标签态"的完整规范和"若将来改侧边导航"的等价映射。

### 4.1 外壳背景处理

```qss
QDialog {
    background: #16111F; color: #FAF6FB; font-family: "LXGW WenKai";
}
QWidget#WizardRoot {
    background: transparent; color: #FAF6FB;
    font-family: "LXGW WenKai"; font-size: 14px;
}
/* 外壳：整窗竖向墨渐变，顶端最亮 */
QFrame#WizardShell {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #1D1729, stop:0.32 #16111F, stop:1 #120E1A);
    border: 1px solid #46385C;
    border-top-color: rgba(205, 184, 255, 85);
    border-radius: 20px;
}
/* 装饰：品牌角的樱色辉光（全窗唯一装饰，也是记忆点） */
QFrame#WizardHeader {
    background: qradialgradient(cx:0.08, cy:0.0, radius:0.9, fx:0.08, fy:0.0,
        stop:0 rgba(255, 157, 190, 30),
        stop:0.5 rgba(183, 166, 255, 14),
        stop:1 rgba(22, 17, 31, 0));
    border: none;
}
/* 底栏：向下收沉，把内容"压"在页面里 */
QFrame#WizardFooter {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(22, 17, 31, 0), stop:1 rgba(16, 12, 24, 150));
    border: none;
}
/* 分割线：樱色起笔、紫罗兰过渡、右端淡出（不再是灰直线） */
QFrame#WizardDivider {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 rgba(255, 157, 190, 130),
        stop:0.26 rgba(183, 166, 255, 75),
        stop:1 rgba(70, 56, 92, 0));
    border: none; min-height: 1px; max-height: 1px;
}
QSizeGrip { background: transparent; width: 18px; height: 18px; image: none; }
```

**品牌与状态：**

```qss
QLabel#BrandMark {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #FF9DBE, stop:1 #FFC48F);
    color: #2B0F1C; border-radius: 14px;      /* 28×28 正圆 */
    font-size: 14px; font-weight: 700;
}
QLabel#BrandName { color: #FAF6FB; font-family: "LXGW WenKai";
                   font-size: 16px; font-weight: 700; }
QLabel#StepLabel {
    color: #D6CBE0; font-size: 12px; font-weight: 600;
    padding: 5px 12px;
    background: rgba(46, 36, 64, 200);
    border: 1px solid #46385C; border-radius: 11px;
}
QLabel#ConfigStatus { min-height: 22px; color: #D6CBE0;
                      font-size: 12px; font-weight: 600; }
QLabel#ConfigStatus[status="error"]   { color: #FF8FA0; }
QLabel#ConfigStatus[status="success"] { color: #6FE0B4; }
```

### 4.2 导航态（顶部标签栏）

```qss
QTabWidget#ConfigurationTabs { background: transparent; border: none; }
QTabWidget#ConfigurationTabs::pane {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #2A2139, stop:0.35 #221A2E, stop:1 #1F1829);
    border: 1px solid #46385C;
    border-radius: 14px;
    top: -1px;
    margin: 0 18px 10px 18px;             /* 不变 */
}
QTabBar { qproperty-drawBase: 0; }
QTabBar::tab {
    min-width: 112px; min-height: 32px;
    padding: 9px 18px;
    margin: 0 6px 0 0;
    color: #A79BB8;
    background: transparent;
    border: 1px solid transparent;
    border-top-left-radius: 12px;
    border-top-right-radius: 12px;
    font-size: 14px; font-weight: 600;
}
QTabBar::tab:hover {
    color: #FAF6FB;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 rgba(255, 157, 190, 34),
        stop:1 rgba(183, 166, 255, 22));
    border-color: rgba(183, 166, 255, 55);
}
/* 选中：顶端一道 2px 实心樱色"点亮边"，与 pane 无缝相接 */
QTabBar::tab:selected {
    color: #FAF6FB;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #FF9DBE, stop:0.06 #FF9DBE,
        stop:0.061 #221A2E, stop:1 #221A2E);
    border-color: #46385C;
    border-bottom-color: #221A2E;
    font-weight: 700;
}
QTabBar::tab:focus { border: 2px solid #CDB8FF; }
QWidget#ConfigurationTabContent { background: transparent; }
QScrollArea#ConfigurationTabScroll { background: transparent; border: none; }
```

选中态用了**四个**冗余信号：位置（点亮边）、颜色（粉）、明度（底色抬起）、字重（700）—— 满足"状态不能只靠颜色表达"。
`primary / surface` = 8.62:1，远超 UI 图形 3:1。

**若将来改侧边导航，映射如下（不改令牌）：**

| 顶部标签态 | 侧边导航等价态 |
|---|---|
| 选中的 2px 顶部樱色边 | 左侧 3px 樱色竖条（= 樱腰线装置 B） |
| `border-top-left/right-radius: 12px` | 全边 `border-radius: 12px`，`margin: 2px 10px` |
| pane 背景 `#221A2E` | 选中项背景 `rgba(46,36,64,220)` |
| hover 扫光 G5 方向 0° | 保持 0° 不变 |
| 红点在标签图标位 | 红点右对齐到导航项尾部，尺寸与配色不变 |

### 4.3 标签页红点提示

现有 `_build_missing_icon()` 画的是纯 8px 红圆点，在"选中标签(#221A2E)"和"未选中标签(透明→外壳渐变)"两种底上边界不同。改为**带墨环的红点**，两种底上都锐利：

```python
@staticmethod
def _build_missing_icon() -> QIcon:
    """带墨环的红点：在选中/未选中标签底上都保持清晰边界。"""
    pixmap = QPixmap(12, 12)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(11, 7, 19))                    # 外墨环 #0B0713
    painter.drawEllipse(QRectF(0.5, 0.5, 11.0, 11.0))
    painter.setBrush(QColor(PALETTE["danger"]))            # #FF8FA0
    painter.drawEllipse(QRectF(2.0, 2.0, 8.0, 8.0))
    painter.end()
    return QIcon(pixmap)
```

`setIconSize(QSize(12, 12))` 不变。红点**永远**与顶部 `ConfigStatus` 文字原因 + 标签 tooltip 同时出现（`wizard.md` 契约，不得只留颜色）。

### 4.4 输入控件族（统一规范）

**材质原则：卡片是"抬起的纸"，输入是"挖下去的井"。** 井底一律 `#100C18`（比 canvas 还深），井沿一律 `border_strong #7C69A0`（≥3:1）。

```qss
QLineEdit, QTextEdit, QPlainTextEdit,
QComboBox, QSpinBox, QDoubleSpinBox {
    background: #100C18;
    color: #FAF6FB;
    border: 1px solid #7C69A0;
    border-radius: 10px;
    padding: 9px 12px;                    /* 不变，保持布局 */
    font-size: 14px;
    selection-background-color: rgba(255, 157, 190, 200);
    selection-color: #2B0F1C;
}
QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover,
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {
    border-color: #CDB8FF;                /* 旧: text_muted 灰。新: 暖紫抬升 */
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border: 2px solid #CDB8FF;
    padding: 8px 11px;                    /* 不变：焦点不引起回流 */
}
QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled,
QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {
    background: rgba(16, 12, 24, 150);
    color: rgba(167, 155, 184, 120);
    border-color: rgba(70, 56, 92, 150);
}

/* 数字步进：暗色 + 内置高对比 chevron（wizard.md 硬性要求） */
QSpinBox, QDoubleSpinBox { padding-right: 34px; }
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {
    subcontrol-origin: border; width: 28px;
    background: #2E2440;
    border-left: 1px solid #7C69A0;
}
QSpinBox::up-button, QDoubleSpinBox::up-button {
    subcontrol-position: top right;
    border-bottom: 1px solid #46385C;
    border-top-right-radius: 9px;
}
QSpinBox::down-button, QDoubleSpinBox::down-button {
    subcontrol-position: bottom right;
    border-bottom-right-radius: 9px;
}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {
    background: rgba(205, 184, 255, 45);
    border-left-color: #CDB8FF;
}
QSpinBox::up-arrow,   QDoubleSpinBox::up-arrow   { image: url("<CHEVRON_UP>");   width: 10px; height: 7px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow,
QComboBox::down-arrow                            { image: url("<CHEVRON_DOWN>"); width: 10px; height: 7px; }

QComboBox::drop-down {
    border: none;
    border-left: 1px solid #46385C;
    background: rgba(46, 36, 64, 170);
    width: 34px;                          /* 不变 */
    border-top-right-radius: 9px; border-bottom-right-radius: 9px;
}
QComboBox QAbstractItemView {
    background: #2E2440; color: #FAF6FB;
    border: 1px solid #7C69A0; border-radius: 10px;
    selection-background-color: rgba(255, 157, 190, 70);
    selection-color: #FAF6FB;
    padding: 4px; outline: 0;
}

QTextBrowser, QTextBrowser#SummaryOutput {
    background: #100C18; color: #D6CBE0;
    border: 1px solid #46385C; border-radius: 14px;
    padding: 12px 14px; font-size: 13px;
    selection-background-color: rgba(255, 157, 190, 200);
    selection-color: #2B0F1C;
}
QTextEdit#LogOutput {
    color: #D6CBE0;
    font-family: "Cascadia Code", "JetBrains Mono", "Cascadia Mono", Consolas, monospace;
    font-size: 12px;
}
```

**QCheckBox / QRadioButton（焦点环从"变字色"改成真正的环）：**

```qss
QCheckBox, QRadioButton {
    color: #FAF6FB;
    spacing: 10px;
    font-size: 14px;
    border: 2px solid transparent;        /* 预留焦点环空间，聚焦时不位移 */
    border-radius: 8px;
    padding: 0px 4px;
}
QCheckBox:focus, QRadioButton:focus {
    border: 2px solid #CDB8FF;            /* 2px 可见焦点环（MASTER 硬性要求） */
    color: #FAF6FB;
}
QCheckBox::indicator, QRadioButton::indicator {
    width: 20px; height: 20px;
    border: 2px solid #7C69A0;
    background: #100C18;
}
QCheckBox::indicator    { border-radius: 6px; }
QRadioButton::indicator { border-radius: 11px; }
QCheckBox::indicator:hover, QRadioButton::indicator:hover { border-color: #CDB8FF; }
QCheckBox::indicator:checked, QRadioButton::indicator:checked {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #FF9DBE, stop:1 #FFC48F);
    border-color: #FF9DBE;
}
QCheckBox::indicator:checked:disabled, QRadioButton::indicator:checked:disabled {
    background: rgba(255, 157, 190, 90);
    border-color: rgba(124, 105, 160, 140);
}
QCheckBox:disabled, QRadioButton:disabled { color: rgba(167, 155, 184, 120); }
```

> **可选增强（需新增两个资源文件，本次不强制）：** 若添加 `meapet/assets/icons/check.svg`（`#2B0F1C` 描边）与 `dot.svg`，再加
> `QCheckBox::indicator:checked { image: url("<CHECK>"); }` / `QRadioButton::indicator:checked { image: url("<DOT>"); }`，
> 勾选态就有"形状"而不只是"颜色"，更贴合无障碍。当前"空井 vs 粉色实心"已构成形状差异，合规。

### 4.5 按钮三级体系

| 级别 | 使用规则 | 背景 | 文字 | 边框 | 圆角/内距/字重 |
|---|---|---|---|---|---|
| **Primary**<br>`#PrimaryButton` | **一屏最多一个**（向导里就是"保存配置"） | G1 `#FF9DBE→#FFC48F` 135° | `#2B0F1C` | `1px #FF9DBE` | `10px` / `9px 22px` / `700`，字号 `15px`，`font-family: DISPLAY` |
| **Secondary**（`QPushButton` 默认） | 测试连接、浏览、重置、取消等一切次要动作 | 90° `#33284A→#281F38` | `#FAF6FB` | `1px #7C69A0` | `10px` / `9px 18px` / `600` |
| **Ghost**<br>`#GhostButton` / `#CloseButton` | 关闭、查看详情、低权重导航 | `transparent` | `#D6CBE0` | `1px transparent` | `10px` / `9px 16px` / `600` |

各态：

```qss
/* Secondary（基础规则，向导全局 QPushButton） */
QPushButton {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #33284A, stop:1 #281F38);
    color: #FAF6FB;
    border: 1px solid #7C69A0;
    border-radius: 10px;
    padding: 9px 16px;
    font-weight: 600;
}
QPushButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #3D3054, stop:1 #302543);
    border-color: #CDB8FF;
}
QPushButton:pressed  { background: #241C32; }
QPushButton:focus    { border: 2px solid #CDB8FF; padding: 8px 15px; }
QPushButton:disabled {
    color: rgba(167, 155, 184, 120);
    background: rgba(46, 36, 64, 150);
    border-color: rgba(70, 56, 92, 150);
}

/* Primary */
QPushButton#PrimaryButton {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #FF9DBE, stop:1 #FFC48F);
    font-family: "LXGW WenKai";
    font-size: 15px; font-weight: 700;
    color: #2B0F1C;
    border-color: #FF9DBE;
}
QPushButton#PrimaryButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #FFB6CE, stop:1 #FFD3AC);
    border-color: #FFB6CE;
}
QPushButton#PrimaryButton:pressed  { background: #FF9DBE; }
QPushButton#PrimaryButton:disabled {
    background: rgba(46, 36, 64, 150);
    color: rgba(167, 155, 184, 120);
    border-color: rgba(70, 56, 92, 150);
}

/* Ghost / 关闭（危险色只在 hover 出现） */
QPushButton#CloseButton {
    background: transparent; color: #A79BB8;
    border-color: transparent; border-radius: 12px;
    font-size: 17px; padding: 0;
}
QPushButton#CloseButton:hover {
    background: rgba(255, 143, 160, 40);
    color: #FF8FA0;
    border-color: rgba(255, 143, 160, 110);
}
QPushButton#CloseButton:focus { border: 2px solid #CDB8FF; }
```

### 4.6 卡片、进度、滚动条、系统对话框

```qss
QFrame#PageCard {                          /* 一级纸面 */
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #2A2139, stop:0.35 #221A2E, stop:1 #1F1829);
    border: 1px solid #46385C;
    border-top-color: rgba(205, 184, 255, 75);
    border-radius: 14px;
}
QFrame#SectionCard {                       /* 二级：抬起 + 樱腰线 */
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #352A4A, stop:1 #2A213A);
    border: 1px solid #46385C;
    border-left: 3px solid rgba(255, 157, 190, 110);
    border-radius: 12px;
}
QLabel#PageEyebrow   { color: #B7A6FF; font-size: 11px; font-weight: 700; }
QLabel#PageTitle     { color: #FAF6FB; font-family: "LXGW WenKai";
                       font-size: 22px; font-weight: 700; }
QLabel#PageDescription { color: #D6CBE0; font-size: 13px; }
QLabel#SectionTitle  { color: #FAF6FB; font-family: "LXGW WenKai";
                       font-size: 16px; font-weight: 700; padding-top: 4px; }
QLabel#FieldLabel,
QLabel#InlineFieldLabel { color: #D6CBE0; font-size: 13px; font-weight: 600; }
QLabel#HelperText    { color: #A79BB8; font-size: 12px; }
QLabel#FontScaleValue {
    color: #FF9DBE; background: #100C18;
    border: 1px solid #46385C; border-radius: 10px;
    padding: 5px 10px; font-size: 14px; font-weight: 700;
}
QLabel[status="success"] { color: #6FE0B4; }
QLabel[status="warning"] { color: #FFD37A; }
QLabel[status="error"]   { color: #FF8FA0; }
QLabel[status="muted"]   { color: #A79BB8; }

QProgressBar {
    background: #100C18; color: #FAF6FB;
    border: 1px solid #46385C; border-radius: 8px;
    text-align: center;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #FF9DBE, stop:0.55 #FFB6CE, stop:1 #FFC48F);
    border-radius: 7px;
}

QSlider::groove:horizontal   { height: 6px; background: #100C18;
                               border: 1px solid #46385C; border-radius: 3px; }
QSlider::sub-page:horizontal { background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                   stop:0 #FF9DBE, stop:0.55 #FFB6CE, stop:1 #FFC48F);
                               border-radius: 3px; }
QSlider::add-page:horizontal { background: #100C18; border-radius: 3px; }
QSlider::handle:horizontal   { width: 20px; margin: -8px 0; background: #FAF6FB;
                               border: 3px solid #FF9DBE; border-radius: 10px; }
QSlider::handle:horizontal:hover   { border-color: #CDB8FF; }
QSlider::handle:horizontal:pressed { background: #FFB6CE; }

QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 4px 2px; }
QScrollBar::handle:vertical { background: rgba(124, 105, 160, 170);
                              border-radius: 4px; min-height: 32px; }
QScrollBar::handle:vertical:hover { background: #A79BB8; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }

QToolTip {
    background: #2E2440; color: #FAF6FB;
    border: 1px solid #7C69A0; border-radius: 8px;
    padding: 6px 10px;
}

> **运行时覆盖（2026-07）：** 原生 `QMessageBox` 的 QSS 无法控制 Windows
> 标题栏，会形成白色标题栏与深色客户区拼接。以下规则仅作为第三方/遗留调用
> 的兼容兜底；MeaPet 自身的信息、警告、错误和确认必须通过
> `meapet/message_dialog.py::MeaMessageDialog` 完整自绘。该窗口继续返回
> `QMessageBox.StandardButton` 值，因此不改变业务判断。

QMessageBox { background: #16111F; color: #FAF6FB;
              font-family: "LXGW WenKai"; font-size: 14px; }
QMessageBox QLabel { background: transparent; color: #FAF6FB; }
QMessageBox QLabel#qt_msgbox_label { min-width: 280px; }   /* 单测断言此选择器存在 */
QMessageBox QPushButton {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #33284A, stop:1 #281F38);
    color: #FAF6FB; border: 1px solid #7C69A0; border-radius: 10px;
    padding: 8px 18px; min-width: 88px; min-height: 36px; font-weight: 600;
}
QMessageBox QPushButton:hover  { background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                     stop:0 #3D3054, stop:1 #302543);
                                 border-color: #CDB8FF; }
QMessageBox QPushButton:focus  { border: 2px solid #CDB8FF; }
QMessageBox QPushButton:default,
QMessageBox QPushButton[default="true"] {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #FF9DBE, stop:1 #FFC48F);
    color: #2B0F1C; border-color: #FF9DBE; font-weight: 700;
}

QFileDialog { background: #16111F; color: #FAF6FB; font-family: "LXGW WenKai"; }
QFileDialog QWidget { background: #16111F; color: #FAF6FB; }
QFileDialog QLineEdit, QFileDialog QComboBox {
    background: #100C18; color: #FAF6FB;
    border: 1px solid #7C69A0; border-radius: 10px; padding: 8px 10px;
}
QFileDialog QPushButton {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #33284A, stop:1 #281F38);
    color: #FAF6FB; border: 1px solid #7C69A0; border-radius: 10px;
    padding: 8px 14px; min-height: 36px; font-weight: 600;
}
QFileDialog QPushButton:hover { background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                    stop:0 #3D3054, stop:1 #302543);
                                border-color: #CDB8FF; }
QFileDialog QTreeView, QFileDialog QListView {
    background: #221A2E; color: #FAF6FB;
    border: 1px solid #46385C; border-radius: 10px;
    selection-background-color: rgba(255, 157, 190, 70);
    selection-color: #FAF6FB;
}
QFileDialog QHeaderView::section {
    background: #2E2440; color: #D6CBE0; border: none;
    border-right: 1px solid #46385C; border-bottom: 1px solid #46385C;
    padding: 6px 8px;
}
```

### 4.7 向导里独立定义的四段样式常量

`STYLE_PAGE_CARD` / `STYLE_INPUT` / `STYLE_BTN_PRIMARY` / `STYLE_BTN_SECONDARY` 是 `WIZARD_STYLESHEET` 的局部副本。**必须与上文对应段落逐字同步**，否则会出现"同一控件两种皮肤"。

---

## 5. 实现清单（按文件）

### 5.1 `meapet/ui_theme.py`

| 位置 | 改什么 |
|---|---|
| `_PALETTE` 字典（L15–35） | 19 个键**逐一替换为 §2.1 的新值**。键名一个不动、不增不删 |
| `RADIUS_SMALL`（L88） | `8 → 10` |
| `RADIUS_MEDIUM`（L89） | `12 → 14` |
| `RADIUS_LARGE`（L90） | `18 → 20` |
| 其余 | `SPACE_*` / `MIN_TARGET_SIZE` / 字体常量 / 所有函数**不动** |

> `RADIUS_SMALL - 1` 在 `theme.py` L312–313 与 `styles.py` L331/336 被用作内角，自动变成 `9`，无需另改。

### 5.2 `meapet/desktop/theme.py`

顶部 `COLOR_*` 常量**全部保持不变**（它们只是 PALETTE 的别名，换值后自动生效）。逐段要点：

| 段 | 要点 |
|---|---|
| `MENU_STYLE`（L35） | 背景改 §3-③ 的竖向 rgba 渐变；`border-radius: 12`；`padding: 6`；item `min-height: 34` / `padding: 8px 28px 8px 14px` / `radius 8` / `margin 2px 4px`；`item:selected` 换成 **G5 扫光** + 粉色边；新增 `item:pressed`；separator 换成渐变发丝线；新增 `QMenu::icon { left: 10px; }`。**保持 `font-size: 14px`**（单测） |
| `DIALOG_STYLE`（L73） | `QDialog` 底色改 canvas；`SizeDialogCard/TimelineCard` 用 **G3** + `border-top-color`；`TurnCard` 从共用规则里**拆出来**单独用 **G4 + 樱腰线**；输入类底改 `#100C18` 并补 `selection-color`；`QPushButton` 基础态改 90° 墨渐变 + 新增 `:pressed` / `:disabled`；`PrimaryButton` 改 **G1**；`GhostButton` hover 改紫罗兰；`QSlider` groove 改深井 + sub-page 改 **G2**；滚动条把手改 `rgba(124,105,160,170)`；全部 `font-weight` 收敛到 400/600/700 |
| `CONSENT_DIALOG_STYLE`（L218） | 保留控件 `padding` 与最小触控尺寸；420×270 / 440 只作为设计基准，窗口由布局提示与屏幕边界决定。`CloudConsentCard` 用 G3 变体 + `radius 20`；`SectionCard` 加樱腰线；校验/倒计时条换新 tint；`AllowUploadButton` 换 G1；`CancelUploadButton:default` 换 `rgba(205,184,255,30)` + 2px focus 色边 |
| `CHAT_COMPOSER_STYLE`（L380） | 按 §3-② 整段替换。**`QLabel#ComposerTitle` 的第一条声明必须仍是 `color:`**（单测 `assertIn("QLabel#ComposerTitle {\n        color: ")` 逐字匹配，含 8 空格缩进）；**必须仍含 `font-family: {DISPLAY_FONT_FAMILY};`** |
| `DIALOGUE_STYLE`（L469） | 按 §3.4 替换。**必须仍含 `QFrame#DialogueBubble`**，**不得出现 `DialogueName` / `DialogueAccent`**，**必须仍含 `font-family: {DISPLAY_FONT_FAMILY};`** |
| `STATUS_PANEL_STYLE`（L505） | 按 §3-④ 替换；`PanelEyebrow` 改 accent、`TierLabel` 改 primary 18px、`StatusCard` 加樱腰线与月光缝、进度条改 G2 |
| `SPLASH_STYLE`（L588） | 按 §3-⑤ 替换；`SplashCard` 用 G3 深变体 + radius 20；`SplashMark` 改 G1 |

### 5.3 `meapet/desktop/widgets.py`

| 位置 | 改什么 |
|---|---|
| `DIALOGUE_TAIL_BASE`（L53） | `28 → 24` |
| `DIALOGUE_RADIUS`（L56） | `20 → 22` |
| `MOOD_BORDER_COLORS`（L68–78） | 替换为 §3.2 的九个新色 |
| `SpeechBubbleFrame._body_rect()`（L178） | `edge = 1.5 → 2.0` |
| `SpeechBubbleFrame.paintEvent()`（L315–337） | 按 §3.1 重写：**三叠墨影** → **外墨环 `#0B0713` 2.6px + 竖向三段渐变填充** → **内樱环 1.6px α235**。移除 `.lighter(108)`，改用显式 hex |
| `SizeScaleDialog.__init__`（L835 之后） | 给 `container`（`SizeDialogCard`）挂 `QGraphicsDropShadowEffect(QColor(6,4,12,195), blurRadius=14, offset=(0,3))` |
| **不要动** | `DIALOGUE_TAIL_SIZE/_DEPTH/_REACH`、`*_WIDTH/_HEIGHT`、`*_PADDING`、`DIALOGUE_STACK_OPACITIES`、`show_text()` 全部尺寸算式、动画时长 |

气泡绘制无需新增 import（`QLinearGradient` / `QPen` / `QColor` 已在 L14–22 导入）；阴影需要 `QGraphicsDropShadowEffect`。

### 5.4 `meapet/desktop/status_panel.py`

| 位置 | 改什么 |
|---|---|
| `paintEvent()`（L175–187） | 按 §4.1 重写：圆角裁切 `radius=20` + 墨纱竖向渐变 + `#7C69A0` 1px 内亮环；无背景图时 fallback 改 `QColor(22,17,31,238)` |
| `_build_ui()` 末尾 | 给 `section1/2/3` 各挂 `QGraphicsDropShadowEffect(color=QColor(6,4,12,120), blurRadius=16, xOffset=0, yOffset=4)` |
| 需新增 import | `QPainterPath, QPen, QLinearGradient` from `QtGui`；`QRectF` from `QtCore`；`QGraphicsDropShadowEffect` from `QtWidgets` |
| **不要动** | 布局边距/间距、`440×620` 设计基准、`refresh()` 逻辑；窗口本身必须可随字体和屏幕调整 |

### 5.5 `meapet/desktop/splash.py`

在 `outer.addWidget(self.card)` 之后加：

```python
from PyQt5.QtWidgets import QGraphicsDropShadowEffect
from PyQt5.QtGui import QColor

shadow = QGraphicsDropShadowEffect(self.card)
shadow.setColor(QColor(6, 4, 12, 200))
shadow.setBlurRadius(18)
shadow.setOffset(0, 3)
self.card.setGraphicsEffect(shadow)
```

其余不动（`setFixedSize(440,300)`、`outer` 边距 12、`progress.setFixedHeight(8)` 全部保留）。

### 5.6 `meapet/desktop/dialogs.py`

| 位置 | 改什么 |
|---|---|
| `CloudVisionConsentDialog.__init__`（L131 `outer.addWidget(card)` 之后） | 给 `card`（`CloudConsentCard`）挂 `rgba(6,4,12,200) / blur 8 / (0,2)` |
| `CaptureScopeConsentDialog.__init__`（同名位置） | 同上参数 |
| **响应式约束** | 删除固定几何；保留 `420×270` / `440px` 设计基准，并由 `resize_dialog_to_content()` 同时计算宽高、限制到屏幕可用区域。所有同意流程的按钮默认值、倒计时与授权判定不变 |

### 5.7 `meapet/desktop/capture_selection.py`

| 位置 | 改什么 |
|---|---|
| `paintEvent()` L157 | `dim.setAlpha(178) → 190` |
| `paintEvent()` L184–185 | 选区双环：先 `QPen(QColor(PALETTE["focus"]), 2)` 画 `selection.adjusted(1,1,-1,-1)`，再 `QPen(QColor(PALETTE["primary"]), 1)` 画 `selection.adjusted(3,3,-3,-3)` |
| `paintEvent()` L193 | `background.setAlpha(235) → 244` |
| `paintEvent()` L196 | `drawRoundedRect(box, 10, 10) → drawRoundedRect(box, 14, 14)` |

### 5.8 `wizard/styles.py`

| 段 | 要点 |
|---|---|
| `STYLE_PAGE_CARD`（L39） | 与 §4.6 的 `QFrame#PageCard` 逐字同步（月光缝 + `border-top-color`） |
| `STYLE_INPUT`（L47） | 与 §4.4 的 `QLineEdit` 家族同步（`#100C18` 井底、`#7C69A0` 井沿、hover `#CDB8FF`、补 `selection-color`） |
| `STYLE_BTN_PRIMARY`（L71） | 换 G1 135° 渐变（旧为 0° 横向）；`:pressed` 改 `#FF9DBE`；`:disabled` 换新 tint |
| `STYLE_BTN_SECONDARY`（L100） | 换 90° 墨渐变；hover 边框由 `text_muted` 改 `#CDB8FF` |
| `WIZARD_STYLESHEET` 外壳段（L129–166） | `WizardShell` 加竖向渐变 + `border-top-color` + radius 20；`WizardHeader` 换 **G6 径向辉光**（原来是纯 `COLOR_BG`）；`WizardFooter` 换向下沉的渐变；`WizardDivider` 换渐变发丝线 |
| 品牌/状态段（L172–205） | `BrandMark` 换 G1；`StepLabel` 圆角 11 + 内距 5/12；字重收敛 |
| 文字段（L206–262） | `PageEyebrow` 改 accent；字号按 §2.4 字阶；`FontScaleValue` 底改 `#100C18` |
| 输入家族（L263–374） | 整段按 §4.4 替换，**保留全部 `padding` 数值**；`QComboBox::drop-down` 补 `border-left` 与底色；`QAbstractItemView` 补 `border-radius` 与 `outline: 0` |
| 勾选段（L375–402） | 按 §4.4 替换。**重点**：`QCheckBox/QRadioButton` 基础规则加 `border: 2px solid transparent; border-radius: 8px;`，`:focus` 改成真正的 2px `#CDB8FF` 环（现状只是变字色，不满足"2px 可见焦点环"） |
| 按钮段（L403–455） | 按 §4.5 三级体系替换 |
| 进度/滑块（L456–495） | chunk 与 sub-page 换 G2；groove/add-page 换 `#100C18`；handle 边框色跟随 |
| 标签段（L496–539） | 按 §4.2 替换：pane 加月光缝、radius 14；tab hover 换扫光；**tab:selected 换"2px 顶部樱色点亮边"**；新增 `QTabBar { qproperty-drawBase: 0; }` |
| 滚动条 / ToolTip（L540–575） | 把手 `rgba(124,105,160,170)`；ToolTip 加 `border-radius: 8px` + 内距 6/10 |
| QMessageBox / QFileDialog（L576–660） | 原生 QMessageBox 规则仅保留为兼容兜底；产品内调用统一转到 `meapet/message_dialog.py`。**`QMessageBox QLabel#qt_msgbox_label { min-width: 280px; }` 仍原样保留**（兼容旧主题断言） |
| 函数区（L664 起） | `styled_message_box()` 只负责转发到共享 `MeaMessageDialog`；其余 `set_status` / `apply_wizard_dialog_style` / `field_label` / `styled_*` / `prepare_accessible_page` 保持既有职责 |

### 5.9 `wizard/app.py`

| 位置 | 改什么 |
|---|---|
| `_build_missing_icon()`（L541–551） | 按 §4.3 改为"墨环 + 红点"双层绘制；需 `from PyQt5.QtCore import QRectF` |
| `__init__` 中 `outer.addWidget(self.container)` 之后（L140） | 给 `self.container`（`WizardShell`）挂 `QGraphicsDropShadowEffect(QColor(6,4,12,205), blurRadius=24, offset=(0,4))` |
| **不要动** | `outer.setContentsMargins(16,16,16,16)`、四个标签的顺序与文案、`setIconSize(QSize(12,12))`、`_refresh_required_tabs()` 逻辑、所有配置读写 |

### 5.10 `meapet/desktop/window_chrome.py`

| 位置 | 改什么 |
|---|---|
| `_setup_tray()` 兜底图标（L49–60） | 现在画的是 `COLOR_ACCENT` 实心圆 + `COLOR_ACCENT_2` 描边 + `COLOR_TEXT` 内圆。换新色后自动变漂亮，**可不改**；若要更贴新方向，把内圆 `COLOR_TEXT` 改为 `PALETTE["on_primary"]`，形成"粉底墨心"的品牌记号，与 `BrandMark` / `SplashMark` 一致 |
| `DangerAction`（L437） | **保留不动**（见 §3-③ 说明：QAction 的 objectName 不参与 QSS，也不应引入纯色危险信号） |

### 5.11 `design-system/MASTER.md`

把「语义色」表的 13 行色值同步为新值，并把「圆角：8 / 12 / 18px」改为「10 / 14 / 20px」，在「Page overrides」列表里加一行指向本文件。其余章节（组件规则、验收清单、PyQt5 映射）**保持原样**。

---

## 6. 回归红线（改动前先读这一节）

以下任一条被破坏，`tests/test_ui_refactor.py` 会红：

1. `PALETTE` 的 19 个**键名**必须原样存在；6 组对比度必须 ≥4.5（§2.2 已自证全部通过）。
2. `CHAT_COMPOSER_STYLE` 必须逐字包含 `QLabel#ComposerTitle {\n        color: `（8 空格缩进，且 `color` 是该规则第一条声明）。
3. `CHAT_COMPOSER_STYLE` 与 `DIALOGUE_STYLE` 都必须包含 `font-family: "LXGW WenKai";`。
4. `DIALOGUE_STYLE` 必须包含 `QFrame#DialogueBubble`，且不得包含 `DialogueName` / `DialogueAccent`。
5. `WIZARD_STYLESHEET` 必须包含 `QMessageBox QLabel#qt_msgbox_label`。
6. `MENU_STYLE`：菜单 `font-size` ≥14px，菜单项实测行高 ≥38px。
7. `CloudVisionConsentDialog` 在 80%–150% 字体缩放下不得小于 `minimumSizeHint()`，不得锁死最大宽度，且 `allow_button.width() == cancel_button.width()`。
8. 短气泡 `< 260×130`、长气泡 `≤ 420×240`；因此 `DIALOGUE_TAIL_SIZE / _DEPTH / _REACH / *_PADDING / *_WIDTH / *_HEIGHT` 一个都不能改。
9. 全部 `objectName` 原样：`WizardRoot` / `WizardShell` / `SplashCard` / `DialogueBubble` / `StatusCard` 及 `theme.py`、`styles.py` 中出现的每一个。
10. 交互目标 ≥44px、菜单项 ≥32px、焦点环 2px —— 本规范全部保留，并在 QCheckBox/QRadioButton 上**补齐了原先缺失的真实焦点环**。

## 7. 验收清单（在 `MASTER.md` 基础上追加）

- [ ] 桌宠气泡分别在**纯白**与**纯黑**壁纸截图上：外墨环与内樱环各自可见，正文无糊边。
- [ ] 聊天输入框、状态面板、右键菜单在两种壁纸上边界清晰（双环装置生效）。
- [ ] 全站数一遍：**每屏只有一个 G1 粉色渐变按钮**。
- [ ] 全站搜索 `font-weight`：只剩 `400 / 600 / 700`。
- [ ] 全站搜索渐变方向：只有 `x2:1,y2:1`（主行动）、`x2:0,y2:1`（材质）、`x2:1,y2:0`（进度/扫光）三种。
- [ ] Tab 键走完向导四个标签页 + 所有表单，每一步都能看到 2px `#CDB8FF` 焦点环（含复选框与单选钮）。
- [ ] 字体缩放 80%–150% 全程无裁切（`scale_stylesheet_font_sizes` 只改 `font-size`，本规范未把尺寸写进 `font-size` 之外的地方）。
- [ ] 缺配置时红点在**选中**与**未选中**标签上都清晰，且顶部文字原因同时存在。
