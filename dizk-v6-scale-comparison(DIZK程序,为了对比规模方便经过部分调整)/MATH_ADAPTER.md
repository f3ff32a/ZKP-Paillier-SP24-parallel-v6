> 这是保留的旧严格 Paillier 转换文档。本版默认运行相同约束规模的合成素域电路，其关系和口径见 SCALE_COMPARISON.md。

# 模 N² 到 DIZK 素域的适配说明

## 被证明的关系

令 M=N²。原 v6 CBatchEnc 导出的关系为：对每个门 i，A_i B_i=C_i (mod M)；对每个线性行 q，sum(Wqa*A+Wqb*B+Wqc*C)=Kq (mod M)。所有 Kq、N、稀疏系数和关系结构公开，ABC 私有。导出包括原项目实际建立的全部 gate/linear，而非只抽出普通加密。

原项目的 Li,j 由原 calculateLjir 从密文生成，仍属于验证者的原预处理。本项目没有把该外部 PRG 实现改成另一个电路；它把预处理后的完整实际算术关系交给两种证明系统。DIZK 证明此关系，不证明原 v6 承诺/挑战/证明字节本身。两种协议具有不同 setup、证明形式和安全假设。

对于原复合环中的 b(b−1)=0，继续按原环乘法门与线性行证明；不会擅自将 b 变成 BN254b 的一个布尔位。原随机数生成规则不等于原关系中的约束，本适配也不额外增加 r<N、gcd(r,N)=1 等原电路未建立的条件。

## 整数表示与范围

取 B=2^64，k=bit_length(M)。每个原 ABC 值拆成 ceil(k/64) 个小端 limbs，每个 limb 的每一位均有 b(b−1)=0 约束，同时用线性等式将 limb 与位重组。最后一 limb 使用实际剩余位数。全局范围为 [0,2^k)。

适配后的 private 值允许出现非 canonical 代表；将其模 M 投影即得到满足原关系的 canonical 值。每个原 canonical 见证都有对应的 lifted 见证，因此这种表示不会改变原环关系的存在性。这里没有通过添加 canonical <M 检查加强原关系。

公开 Kq 去除重复值后同样按 limbs 编码，顺序由原行第一次出现确定；各原行到公开值的映射写入电路结构。Java 导入器独立核对 constant1+Kq limbs 公共前缀与 header 一致，所有原 public K 和其行次序仍被 statement_id 绑定。

## 乘法门

原 canonical 见证使整数 A*B−C=q*M，其中 q>=0 且 q<2^(k+1)。q 使用 k+1 位分解。每对 64-bit limb 的乘积在作者素域中单独约束；其最大整数值小于 2^128，远小于 BN254b 素域模数。

将 A*B−q*M−C 按基 B 展开，对每一列约束：column_t + carry_t − B*carry_(t+1)=0，首尾 carry 为 0。进位可正可负，用 unsigned variable = signed carry + 2^(carry_bits−1) 表示，并对 unsigned variable 做位范围约束。

如果一列非进位部分的绝对值上界为 D，则真实 carry 的绝对值可用 ceil(D/(B−1))+1 控制。compiler 由公开系数和 limb 的最大可能值计算 D，取足够位数，约束形状不依赖私有见证。

## 线性行

将原模 M 系数改写为最小绝对值的带符号整数 w。令 S=sum(wX)，q=(S−K)/M，可为负。设 offset=2*sum(abs(负系数))+2，q'=q+offset 非负并有仅依赖公开系数的位数上界。

使用整数恒等式 sum(wX)+offset*M−K−q'*M=0，展开常数乘 limb 的各列并使用同一进位机制。编译器不会按当前 private witness 是否为 0 来删变量、乘法门或范围约束。

## 排除素域环绕

每条整数列等式生成前，compiler 用所有变量的公开范围计算 sum(abs(coefficient)*variable_max)，并断言严格小于 BN254b 的 Fr：

`17855808334804902850260923831770255773646114952324966112694569107431857586177`

因此 Fr 中的 0 等式不能用一个非零 Fr 倍数冒充整数 0 等式。所有 limb 乘积同样有严格小于 Fr 的上界。若任何列上界超过 Fr，编译立即失败，不生成正式完成输入。

## 绑定、失败与局限

statement_id=SHA256(域分隔符+选定原公开 header 的规范 JSON+linear.tsv 原始字节)，排除 private ABC。witness_id=SHA256(gates.tsv)，仅用于本地实验来源核对。证明文件目录保存私有见证，不能把目录当成公开的零知识证明制品分享。

relation_hash 绑定实际 constraints.tsv；assignment_hash 绑定 actual assignment.tsv。转换开始/结束核对三份原输入文件 SHA；未完成目录带 .incomplete，Java 拒绝。Native proof 的公开挑战、全部稀疏 W/Kq 必须与导出一致；汇总同时检查两侧哈希和 verified。

该转换器采取通用、直接展开的策略，没有做跨门别名消除、专用模幂 gadget、查表范围检查或递归证明优化。它因此可以保留任意原 gate/linear 语义，但会生成很大的电路。本文档给出了适配设计和可检查的边界，并非经过同行评审的安全证明。

BN254b 的 FFT 根支持 2^50 阶域，并不代表普通机器可以处理 2^50 个约束。作者 serial FFT 使用 int 索引，此项目进一步限制 serial 大型输入；正式大电路使用 distributed。作者 FFTAuxiliary 的 int shift assert 对超大 long 域有错误，runner 只禁用该类 assertions，保留输入/范围/验证检查和其他 assertions。作者 fillRDD 要求二次幂 partition；因此正式默认为 16。
