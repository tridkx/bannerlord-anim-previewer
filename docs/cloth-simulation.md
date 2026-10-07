# 布料预览

打开预览器的「显示 → 布料模拟」。布料默认开启，风力默认 0，活动距离倍率默认 1。支持身体/地面碰撞开关、风力、活动距离倍率与重置。

本实现采用浏览器 PBD 直接布料模拟；遵循 [TaleWorlds 官方布料文档](https://moddocs.bannerlord.com/editor/resource-editors/cloth_simulation/) 的活动距离语义。它不是游戏原生求解器的移植。游戏测试由你完成。

## 数据与计算

- `baker/cloth.py` 从最终 TPAC 的元数据读取子网格布料标记、活动距离倍率、ClothingMaterial、模拟频率、模拟网格 GUID 与碰撞体名称。支持 TPAC 1/2、Metamesh metadata 0/1、Mesh subversion 0/1/2，与本机 mbtool 的读取布局一致。未知布局提示警告，该网格不启用物理。
- 只有 `uses_cloth_simulation` 或 `force_enable_cloth` 标记存在的网格才尝试模拟；不会根据材质名或非零 Alpha 猜测布料。
- 每个顶点的最大偏移是 `Alpha / 255 × 资源活动距离倍率 × UI 倍率`。Alpha 为 0 的点精确跟随蒙皮；活动点以实时蒙皮位置为球心受到距离约束。布料 Alpha 不再参与颜色透明度计算，关闭物理时仍保持此语义。
- 只合并位置、Alpha、骨骼索引和权重均一致的重复点，避免 UV 接缝各自飘开。每个子网格最多 16000 个渲染顶点，超出时保留原始蒙皮并提示。
- 使用原始三角拓扑生成边长约束、跨相邻三角形的弯曲近似约束。模拟使用资源 stretching、bending、damping、gravity、linearInertia、airDrag、wind、maxVelocity；游戏的 shearing、anchor、velocityMultiplier、precise 与 dummyParticles 参数保留在缓存中，尚未按原生算法实现。
- 以资源频率（限制 30–240Hz）推进，最多每次更新 12 步，单次累计时间限制为 50ms，避免卡顿时模拟炸开。因此卡顿或极高倍速时不保证物理时间与动画时间完全一致。
- 身体胶囊跟随当前人形骨架，包含躯干、头颈、双腿、脚与双臂；尺寸是预览代理，不是原生 authored collision body。最终优先保持活动距离与固定点约束；如果代理身体与活动范围冲突，仍可能穿模。没有布料自碰撞、三角内部虚拟碰撞粒子或马匹碰撞。
- 每次模拟后重算法线，更新独立的动态位置/法线缓冲，避免再做一次 GPU 蒙皮。关闭布料恢复原来的位置/法线缓冲。

暂停时物理冻结；手动拖动时间轴、切换动画、循环跨起点、隐藏装备、调整活动距离或开关布料时清理历史。自动化可调用 `window.__preview.stepCloth(1/60)`；指定静态 `frame` 的截图没有自动预热，不代表经过一段动画后的布料状态。

## 旧缓存

普通重烘焙会自动写入布料元数据。也可以只补布料设置：

```powershell
python -m baker.cloth TianxiangT5
python -m baker.cloth
```

省略名称时更新所有已有缓存。该操作只替换 MBMG 的 JSON 元数据，几何、权重、顶点色和索引字节保持原样；不需要重导贴图和动画。源 TPAC 不存在的旧缓存会跳过，并在界面提示重烘焙。

带非零 simulation mesh GUID 的映射布料暂不支持，保持原始蒙皮并提示，避免把渲染网格的 Alpha 误当模拟网格的活动半径。没有解码 TCC/TCM 或自动生成映射。

## 验证（2026-10-07）

- Python 元数据测试：三个 Mesh 子版本、布料标记、数值、截断/尾部残留/未知版本拒绝、按名称与 LOD 关联、TPAC/MBMG 往返与缓存更新保留几何字节，5 项通过。
- JavaScript 求解器测试：固定点、活动距离、风响应、暂停、重置、相同时间步序列可重复、禁用/映射回退、胶囊轴上的退化接触，通过。
- Edge 无头 WebGL 实测 TianxiangT5 的 `walk_forward_unarmed`：14 个可见部件，其中 3 个直接布料部件、4544 个粒子。推进 60 步后顶点均有限，活动距离界限误差约 `2.86e-8 m`，开关能恢复 GPU 蒙皮，重置能继续模拟，无页面异常。检查了生成的预览图。
- 补充了 6 个仍有源 TPAC 的已烘焙 Mod 缓存，未发生元数据解析警告；4 个源文件已移走的缓存跳过。

运行方式：

```powershell
python -m unittest discover -s tests -p 'test_*.py'
node tests/cloth.test.mjs
# playwright 需已安装或通过 NODE_PATH 指向已有运行时；测试使用系统 Edge
node tests/browser-cloth.cjs
```

浏览器测试临时启动 8785 端口的预览服务，结束时关闭，只读取本机现有 TianxiangT5 缓存。求解器验证与预览截图不等于游戏内布料验证。
