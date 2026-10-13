# v6 原复合环导出与同实例基线

本目录以现有 `CBatchEnc` 的公共成员为接口，不修改 v6 原源码。导出完整原生约束和实际赋值，供独立的 BN254 非原生算术编译器使用。这里的 TSV 本身不能直接输入 DIZK 的 BN254 R1CS。

在 Ubuntu 18.04 的 Python 3.6、g++、NTL、GMP 环境中运行：

```bash
python3 build_and_export.py \
  --v6-source /absolute/path/ZKP-Paillier-SP24-parallel-v6 \
  --output /absolute/path/results/mc400-r1 \
  --bits 512 --messages 400 --threads 16 --repeat 1 \
  --range-proof-count 128 --slot-bytes 4 --messages-per-batch 15 \
  --run-v6-proof

python3 validate_raw.py --input /absolute/path/results/mc400-r1 \
  --report /absolute/path/results/mc400-r1/raw_audit.json
```

编译器直接链接 `src/cpp/app` 的支持实现，排除 `App.cpp`、`EndToEnd.cpp`，不需要 GTest。不传 `--run-v6-proof` 时，仅运行原始 `encrypt → calculateLjir → calculateLj → wireUp → run`，并作整数自检和文件导出。八个 MC 为 1、10、20、40、80、100、200、400，每个使用独立输出目录。重复序号进入基线记录；种子不随重复序号改变，因此同配置重复可保持同一 statement/witness。

Ubuntu 的开发依赖为 `g++ libntl-dev libgmp-dev`；构建参数为 `-std=c++11 -O3 -pthread -fopenmp -march=native -I<source>/src/cpp`，库放在全部源文件之后：`-lntl -lgmp -lm`。这一入口不使用覆盖率插桩，也不需要 `-lgcov`。`-march=native` 与现有 v6 优化构建一致，二进制应在运行机器上构建；`--portable` 可显式去掉该选项，manifest 保存实际完整参数。原生 driver 显式包含了 algorithm/memory/sstream 等所用标准头，避免依赖 vendored JSON 的间接 include。

本环境未安装 g++/NTL/GMP，C++ 编译和完整基线尚待目标 Ubuntu 实跑。两套 Python 语义测试共 20 项通过，Python 3.6 语法检查通过。这些测试不是实验测量。

## 原生导出格式

`header.json` 的格式固定为 `v6-ring-v1`。`modulus` 是十进制字符串 N²，`paillier_modulus` 是十进制字符串 N。`bits` 是实际 N 位数，`gate_count`、`linear_count`、`messages` 是整数。导出器拒绝实际位数不符的 key，512 位 N 的 N² 为 1023 或 1024 位。另有批参数、普通明文 FNV1a64 指纹、明文最大位数、公共 Q/G、三类 ciphertext 及实例信息。

`gates.tsv` 每行四列、索引连续，所有值均为规范整数 `0 <= value < N²`：

```text
gate_index<TAB>A_decimal<TAB>B_decimal<TAB>C_decimal
```

每个原门满足 `A_i * B_i = C_i (mod N²)`。A、B、C 全部是原电路私有赋值。

`linear.tsv` 每行三列、索引连续：

```text
row_index<TAB>K_decimal<TAB>A:gate:coefficient,B:gate:coefficient,C:gate:coefficient
```

第三列仅列非零稀疏项，可为空。项分隔符是逗号，每项的类型、门号、系数用冒号分隔。系数保留原模 N² 规范值，例如负一写作 `N²-1`。每行满足 `Σ(Wqa*A + Wqb*B + Wqc*C) = Kq (mod N²)`。Kq 向量全部是公共输入，N 和 W 是公共电路参数。非原生编译器可以将规范系数改写为等价的有符号最短代表，但必须保留整个模 N² 关系。

供审计的 `meta.json + matrix.tsv + assignment.tsv` 另给出环内标准形：第 0 个变量为常数 1，`1..Q` 是公共 Kq，随后是 A、B、C 三个私有 N 块。前 N 行为乘法门，后 Q 行为 `(Σ W*ABC - Kq) * 1 = 0`。其 `format=v6-native-ring-v1` 和 `scalar_domain_kind=composite_ring_mod_n_squared` 明确排除 BN254 直接导入。

## 实例绑定与计时

`statement_id` 是以下内容的 SHA256：ASCII 域分隔符 `v6-public-ring-statement-v1\n`，按键排序且紧凑的指定公共 header JSON，LF，以及 `linear.tsv` 原始字节。指定字段依次为 `format, modulus, paillier_modulus, gate_count, linear_count, bits, messages, range_proof_count, slot_bytes, slots_per_message, messages_per_batch, batch_count`。JSON 实际编码时排序键。它绑定 N、全部 W/Kq 和电路配置，排除私有赋值。`witness_id=SHA256(gates.tsv 原始字节)` 另绑定实际 A/B/C。

这些 ID 写入 header、诊断 meta、原生基线 JSON 和 provenance manifest。`source_manifest.json` 记录读取的全部 C++/头文件及导出器 SHA256、构建/运行参数、输出文件 SHA256 和编译前后源文件未变化的检查。

`--run-v6-proof` 复用刚导出的同一个 CBatchEnc 实例，运行原始 `generateProverAndRelease, commit, Y, polyCommit, X, prove`，再从同一公共 key/cipher 构建原始 verifier 并执行原始 `verify`。重建的 ciphertext challenge、逐行 Kq 和完整稀疏 W 系数必须与导出数据一致。文件为 `v6_native_timing.json` 和单行 `v6_native_timing.csv`；`verified` 必须为 true 才能用于有效对照。

完整原生 proof generation 的计时为 `proof_generation_seconds = commit_seconds + proving_seconds`，其中 proving 为 Y、polyCommit、X、prove 四段之和。只拿 prove 核心段与 DIZK 完整 prover 比较会漏掉原生 commit 主成本。`verify_seconds` 包含 verifier Y/X 派生和 verify。key、generator、prover/verifier 构建均另计。原生整数审计和导出文件 I/O 不进入 proof generation/verify；`verifier_prepare_seconds` 包含为了审计绑定 W/Kq 而读取和逐行核对 linear.tsv 的时间，不宜用作原协议纯构建速度。

`proof_pipeline_sum_seconds` 是列出的加密、构建、赋值、生成器、准备、proof、verify 阶段之和，排除导出审计和阶段外清理，不是连续墙钟总时长，也没有旧 CSV 中 `circuitTime/2`。原始 `EndToEnd` 的后续同态聚合/平均值分解不运行。历史 CSV 不属于这里重新生成的公共 N/cipher/W/Kq 实例，不能用于“同实例”速度比。

## 源码语义与域差异

依据当前 `outputs/v6-512-comparison-fixed/project-source/ZKP-Paillier-SP24-parallel-v6`：

| 位置 | 已确认语义 |
| --- | --- |
| PaillierEncryption.cpp:83,189-192 | n2=N*N；getGroupP 返回 n2 |
| PaillierEncryption.cpp:95-107 | 公共群模 Q=f*N²+1 是另外生成的素数；它不是电路算术模数 |
| CEnc.cpp:11,126 | wiring 和 witness 均使用 ZZ_pPush(GP_P)，即复合环 Z_(N²) |
| CEnc.cpp:15-110,131-201 | 固定 N 的重复平方/聚合乘法，最终约束 ciphertext=(1+N*m)*r^N modN² |
| CBase.hpp:18-19 | W/Kq、群参数是 common/public；A/B/C 是 secret/prover inputs |
| CBase.cpp:158-199,201-233 | 批电路 append 移动稀疏 wire 并拼接门；assignValues 拷贝实际赋值 |
| CBatchEnc.cpp:293-438 | 全部普通、range mask、packed encryption 子电路，以及 slot、Lj、packing 线性行 |
| CBatchEnc.cpp:340-358 | bri*(bri-1)=0 在复合环上；不能直接替换为 BN254 布尔变量而声称原关系未变 |
| CBatchEnc.cpp:440-512 | 逐子电路原始 witness 和 slot 赋值 |
| CBatchEnc.cpp:237-250 | Ljir 从公共 ciphertext 拼接后通过 NTL PRG 派生，是 verifier preprocessing |
| EndToEnd.cpp:63-79,96-108 | 结构化普通明文及完整 wire/run、转换到 prover 的时点 |
| EndToEnd.cpp:140-221,438-439 | 当前源码副本的原 commit/prove/verify 流程及最终 isValid 失败抛异常 |
| math/Matrix.cpp:53-75,98-121 | 未存储 sparse cell 视为 0；group 是 reshape，并非新增算术约束 |

最终 DIZK 配置采用作者 BN254b，标量素数为 `17855808334804902850260923831770255773646114952324966112694569107431857586177`。导出 meta 和校验器中的这一素数仅用于目标域诊断，原生 `modulus/ring_modulus` 始终保持 N²。直接改模数没有无损映射：在 Z_(143²) 中，143*143=0；在该 BN254b 域中结果为 20449。`validate_native.py --require-bn254` 因此返回 `DOMAIN_MISMATCH`，从不对原关系自动降模。真正严格对照需要独立的、含范围/进位/商约束的非原生算术编译器。

原始 witness generator 中的 `r<N, gcd(r,N)=1` 和 RjMax 生成规则没有在所审计的 CEnc/CBatchEnc wires 中单独表达为范围/互素约束。严格导出保留实际源关系，不把这些生成规则擅自加强成新的证明 statement。
