# 验证记录与交付边界

## 实际完成的验证

- 作者源码固定提交 `29fb0f78964cd6019029d20228ee0bee3184e64d`，186 个构建来源文件 SHA-256 完整核对；`vendor/dizk` 原文件未修改。
- 使用真实 Amazon Corretto JDK 8 `1.8.0_504` 和 Maven 3.9.9 编译成功，144 个运行依赖 JAR，全部产出类文件版本不高于 Java 8 的 52。下载的 Corretto ZIP SHA 与 AWS 公布 SHA 一致。
- 数学适配与完整格式检查：5 项 compiler 测试，内部覆盖 12 组不同复合环随机关系、真实小 Paillier 加密、1024 位模运算、错误门/线性行、篡改位见证、形状不依赖 private witness 和不覆盖结果。
- 原环/诊断校验器 20 项测试；配对汇总 4 项测试覆盖不同 statement/witness、验证失败、自测数据混入、NaN、输入变化与未完成目录。
- 新增脚本通过 Bash 语法检查，交付 Python 脚本通过 Python 3.6 语法检查。此处实际 Python 执行器为 Windows Python 3.11；不是 Ubuntu Python 3.6 的运行复现。
- 正式 adapter 在 Windows Java8/Spark2.2.2 上完成五次真实 setup/prove/verify：小 Paillier 电路 7,633 约束的 serial 和 local[16] 各一次；1024 位模算术 11,335 约束的 serial 连续两次、local[16] 一次。全部 `verified=true`、退出码0，CSV 输入摘要、完整 repeat、计时和字段核验成功。
- 实际 Java 拒绝检查包括错误素域、未转换原环、公开 K 前缀失配、`.incomplete` 和篡改位见证（serial/distributed）。均退出非零且未写成功 CSV。

## 已发现并修正的问题

原作者曲线类/参数类的静态循环初始化曾使随机 G1 参数为空。适配器在 Spark/Kryo 之前初始化参数类并检查 ONE/ZERO，原作者文件不修改。

更重要的实测缺陷是 Setup 使用随机点的坐标位宽建立固定基窗口。G1/G2 独立测试均得到 coordinate_bits=253、scalar_bits=254、window=11：原 fixed-base MSM 与直接乘法不一致；改用真实 Fr 标量254位后与直接乘法一致。该问题曾导致一个正确算术见证的 serial proof 验证为 false。

因此新增 `comparison.RobustSerialSetup` 与 `RobustDistributedSetup` 两份另存副本，保留 MIT 头与原算法，仅修改包名/类名及每类两行 scalarSize。精确补丁、来源 SHA、回归 Java 源和结果在 `adapter`。原 `zk_proof_systems/.../{Serial,Distributed}Setup.java` 仍保留作者原字节。失败记录及修改前的证据在 `validation` 中完整保留，没有通过随机重试筛选成功证明。

其他明确的作者接口边界包括：serial 常数 LC evaluate 未乘常数系数，用 adapter 的独立 LC subclass 修正；超大 FFT 的 int-shift assert 仅在 FFTAuxiliary 类中关闭；partition 强制二次幂；完整输入/公开值/域与实际 proof verification 全部显式检查。

## 尚未完成的实验

本机没有目标 Ubuntu 的 C++/NTL/GMP 运行环境，WSL 列表为空。因此 `native/export_native_ring.cpp` 已与 v6 真实头文件逐项静态核对，但尚未在 Ubuntu18.04 实际编译运行。同实例原生 v6 proof 的目标运行结果还未产生。

**512bit 的完整 MC=1、10、20、40、80、100、200、400 实验尚未运行。** 所有现有 Java CSV 都是小规模功能验证，标记 `arithmetic_self_test`、`same_statement=false`。Windows 自测的并发调度、JIT 和时长不能用于论文性能对比。

资源估算中的磁盘/内存区间是规划用启发式值；实际完整关系的约束估算必须先在目标环境导出后得到。尚未测量大型任务的峰值 RSS、磁盘 spill 和完成时间。旧 v6 CSV 和 Ma 数据没有混入新严格比较。

这份交付提供完整来源、同实例导出/原生基线、整数转换、作者证明路径、失败检测和配对汇总流程；尚不构成完整目标实验成功，也不构成密码学安全审计。
