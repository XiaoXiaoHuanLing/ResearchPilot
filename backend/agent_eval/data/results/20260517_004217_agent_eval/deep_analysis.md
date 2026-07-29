# Agent 全链路评估 — 首轮测试深度分析

> 日期: 2026-05-17 | 样本: 3 (2×kb_simple + 1×kb_complex) | 基线: Dense(3s) vs Hybrid(7.2s) vs Agent(135s)

---

## 一、总体表现

| 维度 | Agent | Dense基线 | Hybrid基线 | 差距 |
|------|-------|----------|-----------|------|
| 平均延迟 | **135.1s** | 4.0s | 7.2s | Agent慢**33倍** |
| 任务完成率 | **33.3%** (1/3) | N/A | N/A | 极低 |
| 工具调用 | **20.3次/样本** | 0 | 0 | 严重冗余 |
| 首Token延迟 | **19.1s** | <1s | <2s | 慢19倍 |

**一句话**: Agent在简单KB查询上比纯检索慢33倍，完成率只有1/3，效率问题严重。

---

## 二、逐样本根因分析

### 样本1: agent_kb_001 — "GPU显存隔离有哪些方式？" (kb_simple)

| 指标 | 值 | 期望 | 评价 |
|------|-----|------|------|
| 工具调用 | **41次** | 1-4 | ❌ 超出10倍 |
| 迭代轮数 | 5轮 | 1轮 | ❌ 5倍 |
| 总延迟 | **245.2s** | <5s | ❌ 49倍 |
| 完成状态 | recursion_limit | 完成 | ❌ 崩溃 |

**根因链路**:

1. **错误路由**: kb_simple场景应直接走 `search_knowledge`，但Agent先调了 `list_active_kbs` + `get_current_time` + `write_todos` (3次!) → 浪费首轮
2. **KB检索失败 → 未降级到纯搜索**: ollama embedding 502导致 `search_knowledge` 失败，Agent转入 `searcher` SubAgent → 联网搜索
3. **搜索过度**: 搜索了6次 `search_web` (3组×2轮)，抓取15个页面，5次 `get_search_content` → 收集了大量与KB无关的外部信息
4. **SubAgent并发但无统筹**: `task` 工具调用searcher子Agent搜索，同时主Agent也在搜索 → 重复搜索同一关键词
5. **write_todos 滥用**: 13次写todo → 每轮更新进度，但只是状态管理不产出实质内容
6. **recursion_limit**: 41次工具调用超过20/25的recursion限制 → 任务崩溃

**关键问题**: 一个简单的KB查询 "GPU显存隔离有哪些方式？" 被Agent做成了"GPU显存隔离全方位研究报告"。回答质量很高(非常详尽)，但这是**过度执行**——用户只要一个简短答案。

### 样本2: agent_kb_002 — "K8s NetworkPolicy的默认行为是什么？" (kb_simple)

| 指标 | 值 | 期望 | 评价 |
|------|-----|------|------|
| 工具调用 | **18次** | 1-4 | ❌ 超出4倍 |
| 迭代轮数 | 3轮 | 1轮 | ❌ 3倍 |
| 总延迟 | **146.6s** | <5s | ❌ 29倍 |
| 完成状态 | ✅ 完成 | 完成 | ✅ |

**根因链路**:

1. **KB检索成功但相关度极低**: 5次 `search_knowledge`，召回22个节点，但最高相关度仅0.06 → 检索质量差
2. **Reranker未改善排序**: rerank后最高分0.0646 → KB中可能就没有高质量NetworkPolicy文档
3. **过度检索**: 5次搜索+4次get_recall_nodes → 反复尝试不同关键词，但相关度始终上不去
4. **write_todos 5次**: 又是状态管理开销
5. **最终答案质量好**: 尽管检索相关度低，LLM从低质量上下文中推断出了正确答案 → 说明LLM能力补偿了检索不足

### 样本3: agent_kb_003 — "对比MIG和GPU虚拟化在显存隔离方面的差异" (kb_complex)

| 指标 | 值 | 期望 | 评价 |
|------|-----|------|------|
| 工具调用 | **2次** | 4-10 | ❌ 太少(429限流) |
| 迭代轮数 | 1轮 | 2-3 | ❌ 未充分执行 |
| 总延迟 | **13.5s** | - | 实际上什么都没做 |
| 完成状态 | ❌ 429限流 | 完成 | ❌ API限流 |

**根因**: sensenova RPM限流 → 400错误 → 任务直接失败。2次工具调用后即崩溃，无恢复。

---

## 三、6大核心问题 (按影响排序)

### 🔴 P0: Agent过度执行 (影响: 延迟×33, 工具调用×10)

**现象**: kb_simple场景，预期1-4次工具调用，实际20-41次。

**根因**:
- Agent Prompt没有区分"简单查询"和"深度研究"
- DeepAgents框架中，SubAgent(searcher)被无条件激活
- `write_todos` 每轮更新进度 = 纯状态管理开销，不产出实质内容
- Agent不知道何时"够用"就停 → 反复搜索直到recursion_limit

**修复方案**:
1. **Prompt增加简洁模式判断**: 如果用户查询是简单事实型问题，直接走KB检索+蒸馏，不启动searcher
2. **限制write_todos调用次数**: 最多2次(初始规划+完成标记)
3. **增加"信息充分性"早停机制**: 如果首次检索相关度>0.5，直接蒸馏回答，不再继续搜索
4. **工具调用预算**: 根据场景类型预设工具调用上限(kb_simple: 6次, kb_complex: 15次)

### 🔴 P0: LLM RPM限流 (影响: 任务完成率33%)

**现象**: sensenova 429 "rpm exhausted" → 整个任务失败

**根因**:
- deepseek-v4-flash RPM限制较严格
- Agent每轮可调用2-3个工具，每个工具触发1次LLM调用 → 并发3-5 RPM
- SubAgent的LLM调用与主Agent叠加 → RPM翻倍

**修复方案**:
1. **Fallback链生效**: 当前config.py有5层fallback，但SubAgent的LLM可能没走fallback
2. **请求限速**: 在middleware层加request throttle，确保不超过RPM
3. **切换到sensenova-6.7-flash-lite**: 对于简单工具调用(如write_todos/list_active_kbs)用轻量模型

### 🟡 P1: 工具调用冗余 (影响: 工具冗余率15.5%)

**现象**: 重复调用list_active_kbs(7次)、重复搜索同一关键词

**根因**:
- 没有记忆已调用过的工具结果 → 每轮重新查询
- list_active_kbs在KB不变化时结果不变 → 缓存即可
- search_knowledge同一query重复搜索 → 去重

**修复方案**:
1. **工具结果缓存**: list_active_kbs结果缓存5分钟
2. **查询去重**: 同一query+top_k组合，60秒内不重复搜索
3. **上下文传递**: 已获取的KB信息在Agent state中共享

### 🟡 P1: 检索→蒸馏转化率极低 (影响: 0.111)

**现象**: 检索了大量节点但蒸馏出的有用信息极少

**根因**:
- KB文档内容与查询不匹配 → 检索结果相关度低(最高0.06)
- 蒸馏Prompt没有对低相关度节点做过滤 → 把无关内容也蒸了
- Agent不知道"检索结果质量差时应换路径"

**修复方案**:
1. **相关度阈值过滤**: 只蒸馏相关度>0.1的节点
2. **低相关度早停**: 如果首轮检索最高相关度<0.1，自动切换到searcher
3. **蒸馏Prompt优化**: 只输出与query直接相关的事实点

### 🟡 P1: KB检索相关度低 (影响: 搜索轮次增加)

**现象**: "K8s NetworkPolicy默认行为" → 最高相关度0.06

**根因**:
- KB中文档以GPU运维为主，NetworkPolicy可能只有片段提及
- embedding维度2560但语义匹配不够精确
- 没有HyDE(假设性文档嵌入)来改善查询

**修复方案**:
1. **HyDE**: 先让LLM生成假设性答案，再用假设答案做检索
2. **查询扩展**: 将"NetworkPolicy默认行为"扩展为"Kubernetes网络策略 默认拒绝 白名单 ingress egress"
3. **文档覆盖度提升**: 补充K8s网络相关文档到KB

### 🟢 P2: 首Token延迟高 (影响: 19.1s)

**现象**: 用户等待19秒才看到第一个token

**根因**:
- Agent初始化(ChatOpenAI创建+checkpointer) ≈ 5s
- 首轮LLM推理(规划+工具选择) ≈ 10s
- 首轮工具调用(list_active_kbs+get_current_time+write_todos) ≈ 4s

**修复方案**:
1. **跳过无用首轮工具**: list_active_kbs和get_current_time可预加载到Agent state
2. **Agent初始化缓存**: ChatOpenAI实例复用
3. **流式输出改进**: Agent规划阶段先输出"正在查询..."的即时反馈

---

## 四、工具调用分布分析

| 工具 | 调用次数 | 是否必要 | 优化建议 |
|------|---------|---------|---------|
| fetch_page | 15 | 搜索场景必要 | 去重URL+限制并发 |
| write_todos | 13 | ❌ 不必要 | 限制≤2次或移除 |
| list_active_kbs | 7 | ❌ 冗余 | 缓存5分钟 |
| search_web | 6 | 搜索场景必要 | 合并同类查询 |
| get_search_content | 5 | 搜索场景必要 | 批量获取 |
| search_knowledge | 5 | KB场景必要 | 去重+相关度阈值 |
| get_recall_nodes | 4 | KB场景必要 | 批量获取 |
| get_current_time | 3 | ❌ 不必要 | 移除或预注入 |
| task | 2 | SubAgent调用 | 合并到主流程 |
| rerank_recall_pool | 1 | KB场景必要 | 保持 |

**结论**: 61次工具调用中，约23次(37%)是冗余的(write_todos 13 + list_active_kbs重复5 + get_current_time 3 + 搜索去重2)

---

## 五、优化优先级路线图

### Phase 1: Prompt + 行为优化 (0.5天, 预期延迟↓70%)

1. **Prompt增加场景判断逻辑** (kb_simple → 直接检索+蒸馏, 不启动searcher)
2. **write_todos调用限制** (≤2次/任务)
3. **工具结果缓存** (list_active_kbs 5min TTL)
4. **get_current_time预注入** (不作为工具调用)
5. **工具调用预算** (kb_simple: 6次上限, kb_complex: 15次上限)

### Phase 2: 检索质量 + 容错 (1天, 预期完成率→90%+)

1. **RPM限流+自动降级** (429→fallback到sensenova-6.7-flash-lite)
2. **相关度阈值过滤** (>0.1才蒸馏)
3. **低相关度自动切换searcher**
4. **HyDE查询扩展**
5. **搜索去重** (同query 60s不重复)

### Phase 3: 架构级优化 (2天, 预期TTFT↓50%)

1. **RecallPool/SearchPool内存缓存** (替代文件IO)
2. **ChatOpenAI单例复用**
3. **Reranker预加载+常驻**
4. **Agent初始化缓存**
5. **SSE即时反馈** ("正在查询知识库...")

---

## 六、预期优化效果

| 指标 | 当前 | Phase 1后 | Phase 2后 | Phase 3后 |
|------|------|----------|----------|----------|
| 任务完成率 | 33% | 50% | 90% | 95% |
| 平均工具调用 | 20.3 | 8 | 6 | 5 |
| 平均延迟 | 135s | 40s | 25s | 15s |
| 首Token延迟 | 19.1s | 10s | 8s | 4s |
| 工具冗余率 | 15.5% | 5% | 3% | 2% |
| 蒸馏转化率 | 11% | 40% | 60% | 70% |
