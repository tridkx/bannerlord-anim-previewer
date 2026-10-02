# 骑砍2 皮套 Mod 动画预览器

**https://github.com/tridkx/bannerlord-anim-previewer**

不进入游戏，就能看到皮套 mod 在**游戏原版动画**下的实机形态。
人类用浏览器界面看；AI 用命令行出图与自动巡检。

依赖 [`bannerlord-tpac-toolkit`](https://github.com/tridkx/bannerlord-tpac-toolkit)（`mbtool`）读 `.tpac`。

> 📄 本文 = **用法**。数据格式结论、判据设计、踩坑记录在
> **[`docs/technical-notes.md`](docs/technical-notes.md)** —— 改动渲染/烘焙代码前建议先读。

```
preview.bat                       ← 双击即用（自检 → 起服务 → 开浏览器）
preview.bat PitaoYingOutfits      ← 先烘焙指定 mod 再打开
mbpreview.bat --help              ← 命令行入口
```

---

## 它解决什么

皮套 mod 的调试循环原本是「改参数 → 打包 → 进游戏 → 看一眼 → 猜」。
这个工具把中间三步搬到本地，并且**从最终交付的 `pack0.tpac` 倒推**——预览的就是你要发出去的那个文件，
不是某个中间产物。

覆盖的问题类型：

| 维度 | 能发现 |
|---|---|
| 蒙皮 | 关节塌角、模型像刚体一起晃、某段肢体不自然扭折、手脚错位 |
| 动画 | 陷地/浮空、裙摆折痕、穿模、姿势怪异 |
| 贴图 | UV 方向错（五官上下错位）、颜色错位、发丝走样 |
| 材质 | 纯黑/纯白片、镂空失效、双面不对、网格不跟骨骼动 |
| 装备 | **原版身体露出来**、槽位冲突、covers 配错 |

---

## 快速开始

```bash
# 1. 环境自检（找游戏目录与 mbtool）
mbpreview.bat doctor

# 2. 看有哪些可预览的 mod
mbpreview.bat mods

# 3. 烘焙（几何 + 材质 + 贴图 + 装备 + 动画）
mbpreview.bat bake PitaoYingOutfits

# 4. 起服务看
preview.bat
```

第一次烘焙要读取动画清单（4052 个骨骼动画 / 6170 个剪辑），约 1~2 分钟；之后走缓存。

**没烘焙过的 mod 不用先跑命令行** —— 在界面上选中它会自动烘焙（顶部显示进度）。
未烘焙的条目在下拉里带 `（未烘焙，选中后自动烘焙）` 标注。

---

## 命令参考

### 给人：`serve`
```bash
mbpreview.bat serve [--port 8777] [--no-browser]
```
浏览器界面里可以：
- **动画**：搜索 / 按分类筛选 4031 条动画，时间轴拖动、循环；播放速率取自 `AnimationClip` 声明的真实时长
- **倍速**：`0.25× / 0.5× / 1× / 1.5× / 2× / 3×` 快捷按钮 + 任意数值（0.05~10）+ 滑块微调。
  1.00× = 严格按游戏 clip 时长播放；**但游戏内实际观感未必与这个数字一致，
  所以自己拖到舒服为止，设置会自动记住**（localStorage）
- **装备**：按槽位勾选，**同一槽位强制互斥**（游戏规则：一次只能穿一件）；
  标出哪些原版部件**没被遮住会露出来**；非人形骨架的件（马/坐骑）标 ⚠
- **多套装备**：自动按命名前缀识别"套"（实测 XianJian7=2 套、LVBU and DIAOCHAN=16 套），
  一键穿整套；默认穿第一套，且套内同样按槽位互斥
- **显示**：正/背/左/右/顶/脸/脚 视角，骨骼线框，地面网格，调试视图（仅贴图 / 法线 / UV / 仅光照），光照预设，背景色
- **诊断**：一键体检当前组合

鼠标：左键旋转 · 滚轮缩放 · 右键或 Shift+左键平移
快捷键：`空格` 播放/暂停 · `B` 骨骼 · `G` 网格 · `1/2/3/4` 前/左/后/脸

### 给 AI：`shot` / `check` / `inspect`
```bash
# 出图（自带后台服务 + 无头浏览器，不依赖常驻进程）
mbpreview.bat shot --mod PitaoYingOutfits --anim inventory_idle --frame 200 --view left -o a.png
mbpreview.bat shot --mod X --anims inventory_idle,walk_forward_unarmed --frames 0,60,120 --views front,left

# 诊断开关（定位渲染问题时很有用）
mbpreview.bat shot --mod X --debug 1                 # 仅贴图，排除光照干扰
mbpreview.bat shot --mod X --only ying_skin          # 只显示匹配的网格/材质（可逗号分隔）
mbpreview.bat shot --mod X --no-alpha-test           # 关掉镂空，看被丢弃的部分
mbpreview.bat shot --mod X --skin woman              # 换原版体型对照

# 动画形变巡检（不开浏览器，直接算 LBS）
mbpreview.bat check PitaoYingOutfits --frames 6 --max 12

# 数据体检（从最终产物倒推）
mbpreview.bat inspect PitaoYingOutfits

# 搜动画
mbpreview.bat anims 走路
mbpreview.bat anims --category 待机
```

`check` 的判据都经过实测校准，避免误报：
- **边长拉伸 p999 > 4** → 某根骨的蒙皮变换异常（权重映射错到别的骨）
- **z 最低 < −6cm** → 陷地
- **位移离群**（max > 1m 且 > 6×p99）→ 个别顶点被甩飞
- 权重和 ≠255 / 骨骼索引 >27 → 打包环节出错

> ⚠️ 判据的坑：t=0 时姿势≈bind pose，LBS 退化成恒等变换，**所有自检都会全绿**。
> 所以巡检一律用 t>0 的帧；边长统计只看 >1cm 的边（毫米级短边在权重过渡区本就会被相对拉伸）。

---

## 目录结构

```
baker/                  Python 烘焙器与算法
  config.py             路径探测（环境变量 > config.json > 自动找 Steam 库）
  mbtool.py             mbtool 封装
  geometry.py           GDMB 解析 → MBMG 输出 + 蒙皮体检
  skeleton.py           骨架 bind pose + 几何自检
  animation.py          动画解析 → MBAN + 姿态求解（LBS）
  material.py           BC1/BC3/BC4/BC5 解码 + alpha bleed + 材质语义翻译
  equipment.py          装备槽位 / covers 遮盖 / 原版体型
  actions.py            动作集解析 → 动画目录（含真实播放速率）
  bake.py               烘焙主流程 + 共享缓存
  check.py              动画形变巡检
  shot.py               无头出图
  server.py             本地预览服务
cli/mbpreview.py        命令行入口
viewer/                 浏览器界面（零第三方依赖，自写 WebGL2）
data/                   烘焙产物（可删，会重建）
  cache/                跨 mod 共享：骨架 / 动画目录 / 原版部件 / 动画
  mods/<mod>/           每个 mod 一份：manifest + geo + tex
```

---

## 已独立验证的数据格式约定

这些都是本项目用真实数据实测出来的，实现时**不要凭直觉改**：

| 项 | 结论 | 判据 |
|---|---|---|
| 骨架 rest | 4×4 **列主序**，平移列是相对父骨的偏移 | 算出的 bind pose：脚趾 z≈0（踩地）、头 1.57m、左右对称 |
| 动画四元数 | **相对父骨的局部旋转**（不是世界朝向） | 沿链复合后与 bind 偏差 0~12°；当世界朝向用则偏差 83~180° |
| 姿态公式 | `M_i = M_parent · [R(q_i) │ rest_i.translation]`，根骨另加 rootPosition | 脚在地面、走路时左右腿交替、抬脚 27cm |
| 蒙皮顺序 | `M_pose @ inv(M_bind)` | 写反在 bind pose 下同样退化成单位阵（自检全绿），只有动起来才炸 |
| UV 的 V 轴 | **不翻**（本源 top-origin，`glTexImage2D` 第一行正落在 t=0） | 不翻时 89.6% 顶点落在贴图不透明区，翻 V 只剩 4.3% |
| 动画播放速率 | `(实际帧数-1) / AnimationClip.duration`，每个动画都不同 | inventory_idle 84.5、walk 26.4、run 31.2 t/s。★ 不能用 animlist 的 dur 字段（它不等于实际帧数，实测差了整整一倍） |
| 权重 | 每顶点 4×u8，和 ==255 | 从最终 tpac 回读 100% 通过 |
| 装备遮盖 | `covers_*` → 隐藏对应原版皮肤部件 | 装备前后绘制数 41 → 19 |

---

## 保真度的边界（诚实说明）

**做到的**：
- 几何、蒙皮、权重、动画：与游戏同一套数据与公式
- 贴图：从 tpac 里的原始 BC 字节解码（BC1/3/4/5），做了 alpha bleed 防止缩小时走样
- 材质语义：`blendMode` / `alphaTest` / `two_sided` / 顶点色 / 蒙皮标志都按游戏规则处理
- 装备遮盖：复刻 `covers_*` 的隐藏行为

**没做的（后续可加）**：
- **光照不是逐像素复刻**。光照参数取自游戏大气 XML（`item_scene_atmosphere.xml`），
  量配平到 ≈1.0，但没移植游戏的延迟渲染管线与后处理（tonemapping / bloom / SSAO）。
  所以**明暗关系接近，但不等于实机截图**。
- 游戏 shader 源码在 `Shaders/Sources/`（844 个文件，含 PBR/GGX），要更保真可以移植——还没做
- 只支持人形骨架（28 骨）；武器/盾牌的持握点、多角色同屏、布料物理未做
- 原版身体的体型参数（`BodyProperties` 的 build/weight/age）用的是默认值

**已知差异**：`PitaoYingOutfits` 的肩膀处有一片深灰——已逐层排查确认是
`ying_cloth_d` 贴图在该 UV 区域的真实颜色（各材质单独渲染都正常、像素 diff 仅 0.09%），
不是渲染错误。若实机不是这样，请反馈。

---

## 环境要求

- Python 3.10+（numpy / Pillow；出图额外需要 playwright）
- 一个能构建的 `mbtool`（本项目的 tpac 读写后端）
- 《骑马与砍杀2：霸主》本体（读骨架、动画、原版身体、装备定义）

路径解析顺序（**项目可以随意搬移**）：
1. 环境变量 `BANNERLORD_DIR` / `MBTOOL`
2. 项目根的 `config.json`
3. 自动探测：注册表找 Steam → 解析 `libraryfolders.vdf` → 扫各库目录
4. `mbtool` 还会找 PATH 与 `../mb-tools/...`（默认同级布局）

```jsonc
// config.json（可选，放在项目根）
{
  "game_dir": "D:/SteamLibrary/steamapps/common/Mount & Blade II Bannerlord",
  "mbtool": "D:/mb-tools/mbtool/bin/Release/net9.0/mbtool.exe"
}
```


---

## 常见问题

**Q：某个 mod 打开特别慢（一两分钟）**
只可能是它在**自动烘焙**（界面左上角会显示进度）。烘焙一次之后就是秒开。
如果明明烘焙过还慢，多半是 mod 名里有空格、而请求路径没被正确解码 ——
这一类问题在 v0.1.1 已修（服务端 `translate_path` 漏了 `unquote`）。

**Q：装备列表里有的件标着 ⚠坐骑**
那个网格绑的不是人形骨架（实测赤兔马用的是 28~31 号骨，而人形只有 0~27），
预览器驱动不了它，勾上姿势必然不对。默认穿戴会跳过这类件。

**Q：启动时出现 `'xxx' 不是内部或外部命令`**
已修。根因是**控制台代码页是 GBK，而 `.bat` 里写了 UTF-8 中文注释** —— cmd 按 GBK 解码 UTF-8
字节会错位，错位后可能吐出 `&` `|` `>` 这类字符，于是被当成命令分隔符，
后面的乱码片段就被当作命令执行了。
现在两个 `.bat` 都是**纯 ASCII**，所有中文提示一律由 Python 输出。
**自己改这些 bat 时请保持纯 ASCII**（想写注释就用英文）。

**Q：动画看起来比游戏快 / 节奏不对**
游戏导出的动画**第 0 帧是绑定姿势（A-pose）**，第 1 帧才是动画真正的起点
（`inventory_idle` 0→1 跳 162°、`walk` 跳 67°）。烘焙器会自动识别并跳过，
时间轴从帧 1 开始、循环不会回到那一帧。若某动画的循环里出现"抽一下"，说明该帧没被跳过。

速度基准是 **1.00× = 严格按游戏 `AnimationClip` 声明的时长播放**
（实测 `inventory_idle` 15.00 秒一循环、`walk` 1.40 秒）。若观感与游戏不同，
直接拖速度滑块校准，**设置会记住**。

**Q：角色看不见 / 忽隐忽现**
已经堵掉的几个原因（若仍有请反馈）：
- 切到加载失败的动画时，旧版会弹一个**无法关闭的全屏错误层**把角色永久盖住 —— 现已改成可关闭面板，且单个动画失败只弹提示
- 贴图请求既不成功也不失败时会**永久卡住加载遮罩** —— 现已加 15 秒超时
- 播放速率算错（慢一倍）导致动画卡在某个姿势 —— 已修
- 弱显卡上骨骼 uniform 数组超配额会让 shader 链接失败、uniform 静默失效 —— 已把上限收到骨架实际需要的 28
- 镂空边缘在 mipmap 下逐帧时有时无 —— 已启用 alpha-to-coverage
- 部分驱动上 `preserveDrawingBuffer` 会与抗锯齿打架导致闪烁 —— 已关闭

自查：画面左上角显示当前动画与帧号，帧号在动说明渲染循环正常；
切到「诊断」页能看到渲染器名称、uniform 配额、MSAA 采样数。

**Q：肩膀/某处有深灰或破洞**
先切到「显示 → 仅贴图」排除光照干扰；再用「诊断」页看材质与贴图绑定。
如果确认是贴图/UV 的问题，多半是 mod 数据本身（例如某些顶点的 UV 落在贴图透明区，
会被 `alphaTest` 丢弃）。命令行可以逐层排查：
```bash
mbpreview.bat shot --mod X --only ying_skin --debug 1 -o layer.png
mbpreview.bat shot --mod X --no-alpha-test --debug 1 -o noat.png
```


---

## 给 AI 的调试接口

无头环境下除了命令行，页面里还暴露了 `window.__preview`，方便脚本驱动与断言：

```js
window.__ready                    // true = 加载完成（headless 截图应等它）
window.__preview.cam              // {az, el, dist, target}
window.__preview.frame            // 当前帧号（NaN 说明渲染循环出问题）
window.__preview.speed            // 当前倍速
window.__preview.visibleMeshes    // 可见网格数（0 = 什么都没渲染出来）
window.__preview.setSpeed(0.5)    // 设倍速
window.__preview.view('left')     // 切视角
```

URL 参数（等价入口）：

```
?mod=<mod名>&anim=<动画key>&frame=<帧号>&view=<front|back|left|right|top|face|feet>
&equip=all|none|<逗号分隔的装备id>&skin=man|woman&speed=<倍速>
&vanilla=0|1&bones=0|1&grid=0|1&debug=0..4&light=item|day|night|studio&only=<材质名>&noalphatest=1
```
