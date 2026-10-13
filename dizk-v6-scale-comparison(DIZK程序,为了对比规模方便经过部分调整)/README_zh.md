# DIZK 与 v6：相同约束规模的单机比较项目

本版供现有 **Ubuntu 18.04、16 vCPU、约 23 GiB 内存**虚拟机先试跑。保留 DIZK 作者的实际 Setup/Prove/Verify，使用 Spark `local[16]`；每次只运行一个实验点，默认 Java 堆 **12 GiB**。

**比较范围已经改变：匹配约束数量，不证明同一 Paillier 计算。** v6 仍是你原来的 512-bit Paillier 实验；DIZK 使用 BN254b 素域的合成链式电路。乘法约束数和线性约束数分别匹配 v6 的 N 和 Q，总数为 N+Q。电路拓扑、算术域、公开输入数量、协议和安全假设不同，结果不能写成“同一 Paillier 计算下 DIZK 比 v6 快/慢多少倍”。

原 `dizk-v6-strict-comparison` 项目和你的原始实验数据保持原样。本目录的 `run_512.sh`/`estimate_512.sh` 也已经指向新的规模模式。

## 1. 安装与构建

将这个 ZIP 解压为 `~/dizk-v6-scale-comparison`，与旧项目分开放置。

```bash
cd "$HOME/dizk-v6-scale-comparison"
sudo apt-get update
sudo apt-get install -y openjdk-8-jdk maven python3 time unzip
export JAVA_HOME=/usr/lib/jvm/java-8-openjdk-amd64
export PATH="$JAVA_HOME/bin:$PATH"
bash runtime/scripts/diagnose.sh
bash scripts/build_scale.sh
```

固定 JDK 8、Spark 2.2.2、Scala 2.10；Spark 由 Maven 下载，无需另装。首次构建需要网络。Ubuntu 18.04 软件源问题需按你虚拟机的实际配置处理，本项目不改系统源。新构建默认放在 `build/dizk-scale`；不要把旧 strict 项目的 JAR 复制过来。构建目录已存在时请选择新目录或使用原已完成的新版本构建。

## 2. 先查看规模，再只跑 MC=1

```bash
bash scripts/plan_512_scale.sh --output ./results/scale-plan-01

bash scripts/run_512_scale.sh \
  --output ./results/scale-mc1-t16-r1 \
  --points 1 --threads 16 --partitions 16 --repeat 1 --heap-gb 12
```

`plan` 只输出 `scale-plan.json`，不会生成证明或性能数据。第二条命令才进行真实 Setup/Prove/Verify。MC=1 成功后，再执行全部八点：

```bash
bash scripts/run_512_scale.sh \
  --output ./results/scale-512-t16-r1 \
  --threads 16 --partitions 16 --repeat 1 --heap-gb 12
```

默认实验点为 **1、10、20、40、80、100、200、400**，与你原数据一致；不是遍历全部 400 个整数。每点一次，点之间依次运行。输出目录必须是新目录，失败后也不要把新数据写进旧目录。

默认参考表已经放入 `reference/v6_full_t16_r1.csv`，你不用另行复制旧 v6 CSV，也不需要再次编译运行 v6。它是你之前的实测记录，**本版不会重新测量 v6**。CSV、原日志、环境和验证代码来源均带 SHA-256 记录。

## 3. 内存与磁盘

运行前检查：

```bash
free -h
df -h .
```

驱动在 Linux 读取 `MemAvailable`，要求至少有“堆大小 + 4 GiB”可用内存；默认 12 GiB 堆需要至少 16 GiB 可用内存。此检查只确认启动余量，不能保证全部大点完成。保留足够磁盘给输入、Spark 临时数据和 spill，试跑时建议先留 20 GiB 以上空闲，实际需求以运行记录为准。

安装 GNU `time` 后，每点的 `dizk-maxrss-kib.txt` 记录 Java 启动任务的最大 RSS；它是进程常驻内存观测值，单位 KiB，不是 Java 堆大小，也不包含之前生成输入的 Python 峰值。缺少 `/usr/bin/time` 时仍可运行，RSS 标为未测量。

12 GiB 是本版的起始配置，**尚未在你的 Ubuntu 虚拟机完成八点验证**。如果 MC=1 即失败，查看该点 `dizk/run.log`、`exit-code.txt` 与根目录失败记录后再调整；不要恢复旧版 512 GiB 堆示例。增加 `--max-constraints` 只修改约束预算，不增加内存。默认预算 2,000,000，大于本次八点的最大值。

## 4. 如何读取结果

全部指定点真实验证通过后，根目录生成 `SCALE_BENCHMARK_COMPLETE.txt`。结果表中 `scope=constraint_scale_benchmark`、`same_statement=false`，DIZK 的 `paillier_bits=0`、`reference_paillier_bits=512`，避免把参考表的位数误写成 DIZK 在执行 512-bit Paillier。

结果保留每点 `input/header.json`、`constraints.tsv`、`assignment.tsv`，以及 `dizk/results.csv` 和 `run.log`。根目录提供 `comparison.csv` 和 `summary.json`；中途失败仅保留已完成点和失败证据，不写全程成功标记。

主时间比值使用：

`DIZK prove_seconds / (v6 commit_s + v6 prove_s)`

v6 的 commit 和 prove 必须合在一起对应完整证明生成。DIZK 的 circuit-specific Setup、Verify、输入准备、RDD materialize 和 Spark 启动分别列出。该比值只能描述相同约束计数下这两份实现的成本；一次运行只能作初步观察，不能据此声称并行加速比或相同安全强度。

更多字段和八点规模见 [SCALE_COMPARISON.md](SCALE_COMPARISON.md)，本次验证边界见 [VALIDATION.md](VALIDATION.md)。

## 来源与保留的实现

- [作者源码 scipr-lab/dizk](https://github.com/scipr-lab/dizk)，固定提交 `29fb0f78964cd6019029d20228ee0bee3184e64d`。`vendor/dizk` 原字节、MIT LICENSE 和 AUTHORS 保留。
- [DIZK 论文，USENIX Security 2018](https://www.usenix.org/conference/usenixsecurity18/presentation/wu)。本项目是单机 Spark 模式，不复现其多节点集群实验。
- 继续使用已有的 `RobustSerialSetup`/`RobustDistributedSetup` 适配：把固定基窗口的标量位宽固定为实际 Fr 的 254 位，避免原实现坐标位宽随机不足造成有效证明失败。作者原文件未改；来源与 diff 在 `adapter`。
- 旧严格转换器、native 导出器及其小型自测源码保留，供追溯；[MATH_ADAPTER.md](MATH_ADAPTER.md) 和 `validation/LEGACY_STRICT_VALIDATION.md` 描述的是旧严格模式，不能用于解释本版合成电路的实验结果。
