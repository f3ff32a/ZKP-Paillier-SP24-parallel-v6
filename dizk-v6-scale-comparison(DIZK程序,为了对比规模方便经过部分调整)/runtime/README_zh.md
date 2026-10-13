# DIZK 单机运行环境与计时审计

本模块在 Ubuntu 18.04 的一个虚拟机内运行作者 DIZK 的串行实现或 Spark `local[N]` 实现，使用作者的 BN254b 大规模曲线实现。正式输入是用户 v6 的 Paillier 关系经过非原生模运算电路转换后的同一陈述，`scope=v6_ring_lifted`、`same_statement=true`。直接把原复合环的系数降模到 BN254b 标量域会改变陈述，不能用于正式比较。单机测试也不能声称复现了论文的多节点结果。

## 来源及固定依赖

- 作者论文：[USENIX Security 2018: DIZK](https://www.usenix.org/conference/usenixsecurity18/presentation/wu)。Howard Wu、Wenting Zheng、Alessandro Chiesa、Raluca Ada Popa、Ion Stoica，pp. 675–692。论文的扩展目标是跨机器计算集群。
- [论文全文](https://www.usenix.org/system/files/conference/usenixsecurity18/sec18-wu.pdf) §9（pp. 684–685）使用单机 r3.large（2 vCPU、15 GiB）以及 10/20 台 r3.8xlarge 的集群，并说明为大电路另生成 2-adicity=50 的曲线。这里使用对应作者 BN254b 类，Fr 模数为 `17855808334804902850260923831770255773646114952324966112694569107431857586177`；必须满足 `constraints+primary_inputs <= 2^50`。常用 BN254a 的 `s=28` 不足以承载本次大电路，不能靠增加内存解决域上限。
- 作者源码：[SCIPR Lab / dizk](https://github.com/scipr-lab/dizk/tree/29fb0f78964cd6019029d20228ee0bee3184e64d)，固定提交 `29fb0f78964cd6019029d20228ee0bee3184e64d`，MIT 许可，原 LICENSE 和 AUTHORS 保留。
- 作者 [pom.xml](https://github.com/scipr-lab/dizk/blob/29fb0f78964cd6019029d20228ee0bee3184e64d/pom.xml) 使用 `spark-core_2.10:2.2.2` 和 `spark-sql_2.10:2.0.0`。这里仅在新工作副本里采用独立 POM，统一为 `2.2.2`；作者原 POM 保留为 `upstream-pom.xml`。`_2.10` 是 Scala 二进制版本，不能解释为 Spark 2.10。
- [Spark 2.2.2 官方文档](https://spark.apache.org/docs/2.2.2/) 说明可在单机运行，`local` 为一个线程，`local[N]` 为 N 个线程；Scala 2.10 在这一版本仍有兼容构建，但已经弃用。运行使用完整 JDK 8，不使用当前 Java 21 直接运行这个旧 Spark 版本。

依赖为完整 JDK 8、Maven（建议 3.6.3 或 3.8.8；3.9.9 也可在 JDK 8 下运行）、Python 3.6+、Bash。脚本使用 Python 标准库。Maven 从 HTTPS Maven Central 下载 Spark/Hadoop/Scala 等运行 JAR；不需要另装 Spark 集群、Scala 编译器、AWS、Docker 或启动 spark-ec2。新 POM 保留作者所选 Scala 2.10，不把二进制不兼容的 Scala 2.11 JAR 混进 classpath。构建时需要联网，完成后本地运行无需访问集群。

Ubuntu 18.04 可执行 `sudo apt-get update` 和 `sudo apt-get install openjdk-8-jdk maven python3` 安装依赖。先用 `java -version`、`javac -version`、`mvn -version` 检查都指向 Java 8；如果装有多个 JDK，可设置 `JAVA_HOME` 并把 `$JAVA_HOME/bin` 放到 PATH 前面。`MAVEN_BIN` 与 `PYTHON_BIN` 可选择已有工具路径，脚本不会自动安装或切换系统工具。构建使用本模块的 settings 文件和副本内 `m2` 缓存；`MAVEN_REPO` 可指定其他任务缓存，不改用户全局 Maven 配置。

## 构建及运行

以下路径是示例；`--dest` 与 `--output` 必须是不存在的新目录。构建会核验 186 个固定作者文件的 SHA-256，复制完整作者材料及 Java 源，新增 comparison 模块，替换副本中的 POM；原始源不被改动。构建跳过作者测试，只编译主源与适配模块。

```bash
bash dizk-runtime/scripts/diagnose.sh --require-maven
bash dizk-runtime/scripts/build.sh \
  --upstream dizk-upstream --adapter dizk-adapter --dest prepared-dizk
bash dizk-runtime/scripts/run.sh \
  --prepared prepared-dizk --input lifted-input-mc1 \
  --mode distributed --threads 16 --partitions 16 --repeat 1 \
  --heap-gb 4 --run-id mc1-dist-r1 --output run-mc1-dist-r1
bash dizk-runtime/scripts/run.sh \
  --prepared prepared-dizk --input lifted-input-mc1 \
  --mode serial --threads 16 --partitions 16 --repeat 1 \
  --heap-gb 4 --run-id mc1-serial-r1 --output run-mc1-serial-r1
```

串行行记录 `requested_threads=16`、实际 `threads=1`、`partitions=0`；Spark 行记录实际 `local[16]` 并校验 master，partitions 单独记录。线程数不是节点数，也不是物理核心数。应在实验记录中同时保留 VM 可见 vCPU、宿主机 CPU、内存、VM 配额/亲和性、JDK 构建版本和 heap 设置。`--heap-gb 4` 只是脚本初始值；按真实电路元数据与 VM 内存调整，不能用相同 MC 推定资源足够。串行导入默认拒绝 `variables+constraints` 超过 2,000,000 的输入，可用 `--serial-max-elements N` 明确调整；这不是内存需求上限的保证。MC400 是否可运行应根据实际约束/变量/非零项计数和试跑记录决定，不预先宣称固定约束数或可完成。

输入目录必须包含 `header.json`、`constraints.tsv`、`assignment.tsv`，header 的 `format=dizk-r1cs-v2`，其中 `variables`、`constraints`、`primary_inputs` 为整数。格式、标量域和严格比较标识由 `check_input.py` 检查；数学满足性和完整格式由适配模块检查。输入文件在运行前后均记录并复核 SHA-256。CSV 必须覆盖每个请求的 repeat，所有 proof 均 `verified=true`，模式、线程、partitions、输入 hash、源提交及时间字段必须匹配。正式运行只有完成这些检查才写 `COMPLETE.txt`；失败保留 `run.log`、退出码、环境记录供诊断，不写完成标志。合成测试只能显式指定 `--synthetic-smoke`，其 scope 为 `synthetic_smoke`。真实算术 fixture 必须显式指定 `--adapter-self-test --input DIR`，scope 为 `arithmetic_self_test`。两种自测均记录 `same_statement=false` 并仅写 `SELF_TEST_COMPLETE.txt`，不能并入正式比较。

## 原 profiler 的计时陷阱

1. [ZKSNARKProfiling.java](https://github.com/scipr-lab/dizk/blob/29fb0f78964cd6019029d20228ee0bee3184e64d/src/main/java/profiler/profiling/ZKSNARKProfiling.java) 调用作者 `R1CSConstruction`，固定 `numInputs=1023`。它不是用户 Paillier 关系的导入器。
2. [R1CSConstruction.java](https://github.com/scipr-lab/dizk/blob/29fb0f78964cd6019029d20228ee0bee3184e64d/src/main/java/profiler/generation/R1CSConstruction.java) 从随机初值生成交替加法/乘法链，并追加平方和约束。约束数量相同不意味着系数、变量、公开输入、见证及实际工作量相同。必须使用正式输入转换后的实际 R1CS。
3. [Profiler.java](https://github.com/scipr-lab/dizk/blob/29fb0f78964cd6019029d20228ee0bee3184e64d/src/main/java/profiler/Profiler.java) 的规模参数是 `2^参数`，不是直接的消息数或约束数；executor/core 参数被写入 Configuration 元数据，本身没有保证实际 Spark 资源。无参入口 `master=local` 只有一个线程。部分分支在创建 SparkSession 后才尝试配置序列化器。
4. 原 `Setup`、`Prover`、`Verifier` 外层计时包含相应阶段内的日志，排除了前面的输入构造；分布式 RDD 的延迟执行需要显式 materialization。`DistributedProver` 另有内层 `Proof` 计时，与外层 `Prover` 重叠，不能相加。
5. [Configuration.java](https://github.com/scipr-lab/dizk/blob/29fb0f78964cd6019029d20228ee0bee3184e64d/src/main/java/configuration/Configuration.java) 默认 seed=5、secureSeed=null、debugFlag=false；这是实验配置。原 CSV 表头写入代码被注释，输出错误只打印异常，不保证实验失败退出。原最终验证用了 Java `assert`，未启用 `-ea` 时可能不阻止错误的成功退出。本模块同时启用 `-ea`、适配模块显式验证及 CSV 验证。
   适配器实际为每次重复设置新的 32 字节 secureSeed，随机参数生成仍由作者算法完成。
6. 作者 `FFTAuxiliary` 的 long 域检查中使用 `1 << log2(m)` 的 int 表达式，大域会出现错误的 assert。运行命令保留全局 `-ea`，仅对这个类使用 `-da:algebra.fft.FFTAuxiliary`；域上限、输入满足性和证明验证由适配模块显式检查，作者文件保持原字节。作者 `Utils.fillRDD` 对非二次幂 partition 的补余数逻辑也不适合本实验，因此脚本和适配器只接受二次幂 partitions（如 1、2、4、16）。
7. 已通过独立 `FixedBaseMSM` 与直接群乘法的对照计算确认：作者两种 Setup 取随机曲线点坐标 `bitSize()` 作为标量位宽，在 253 位坐标与 window=11 时会截掉 254 位 Fr 标量的最高位。适配模块新增 `RobustSerialSetup` 和 `RobustDistributedSetup`，分别复制完整作者实现并保留 MIT 头，只将两个 scalarSize 表达式改为 Fr 模数位长 254。作者原 Setup 文件保持原字节；适配包附精确 patch 和来源 SHA。此修正属于本次采用的 DIZK 实现变体，论文应披露，不能把未经修正的失败证明作为有效性能样本。
8. 正式计时建议各重复独立启动 JVM，使用 `--repeat 1` 和不同 `--run-id`/`--output`。`--repeat N` 在同一 SparkContext 中运行，作者隐藏缓存部分依赖 GC 释放，多个重复间可能有资源影响；Spark 启动时间也只计入第一次重复。连续重复可用于功能回归，但不能忽略这些条件解释性能。

## 对比解释

记录 `input_prepare_seconds`、`spark_start_seconds`、`rdd_materialize_seconds`、`r1cs_validate_seconds`、`setup_seconds`、`prove_seconds`、`verify_seconds` 以及 `proof_system_seconds=setup+prove+verify` 与 `total_task_seconds`。这些字段按适配模块的实际测量定义解释，不能把 v6 的局部 Commit 行时间与 DIZK 全 setup/prove/verify 任意对比。v6 与 DIZK 的协议、信任 setup、曲线/模数不同，正式配对目标是同一 Paillier 陈述和相同输入，报告各自完整生成/验证阶段及前处理开销。Paillier 512 bit 与 BN254b 曲线名字中的数字也不是同一种安全参数。

本模块的静态语法、源文件完整性与失败检查由交付者核验；真实 Java 编译、proof 和 Ubuntu 18.04 执行状态必须以主交付中的实际测试记录为准，不以 ZIP 可读或 CSV 存在替代。
