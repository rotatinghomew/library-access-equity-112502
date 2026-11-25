# 分析口径与使用说明

本项目在 `input_contract.load_bundle` 之上提供解释层。原始 JSON 不被改写；
所有分析结果都能回到具体记录或明确标注"资料缺失"。

## 一、为什么不直接用总量排名

县域图书馆常用的"总借阅量 + 活动人数"会把三类真实服务漏掉或算错：

| 现象 | 处理 |
| --- | --- |
| 流动书车在偏远乡镇服务 | 记为 `mobile_bus` 服务方式，单独进入覆盖矩阵 |
| 家庭成员代借、共用账号 | 只确认"家庭层面发生服务接触"，计入借阅交互，但不折算为多个独立读者 |
| 线上访问日志 | 只记"打开/访问"，完成阅读必须由独立字段确认；不与到馆人数相加 |
| 到馆记录缺失 | 标为 `missing_record` / `unknown_volume`，不填 0，也不当作未提供服务 |
| 分馆搬迁、下架、活动改版 | 用事件日期对照 `active_from`/`closed_at`，历史报告按当时资源解释 |

### 人数区间 `CountBand`

`CountBand(low, high=None)` 表示"至少 low 人，上界未知"。规则：

- 每个已知 `member_key` 至少一位读者；
- 没有任何已知成员的家庭至少一位读者；
- 所有未关联身份的借阅合并计 1 个下界（彼此可能是同一人）；
- 存在 `proxy`、`unknown` 或未关联借阅时，上界保持 `None`；
- 授权资料只能帮助核对家庭登记成员，不能证明代借的使用人，所以不抬高下界，
  也不封闭上界。

## 二、覆盖状态（乡镇 × 服务方式）

`build_coverage` 输出如下状态：

| 状态 | 含义 | 是否缺口 |
| --- | --- | --- |
| `observed` | 记录完整 | 否 |
| `partial` | 有记录但质量不完整 | 是（暂定） |
| `unknown_volume` | 有记录但人数缺失 | 是 |
| `missing_record` | 按当时可用资源本应有记录却未找到 | 是 |
| `unconfirmed` | 只有提议中的活动，未确认 | 是（不算已服务） |
| `no_opportunity` | 报告期没有该服务机会 | 否 |

`available_modes` 由当时的启用分馆、有效路线、活动记录推导；没有机会的
服务方式不会被拿来和别的乡镇比较"缺口"。

## 三、证据报告 `build_report(bundle, as_of=...)`

- 只统计 `as_of` 当日及以前的事件；之后的记录不回溯。
- 每个乡镇输出：机会范围、借阅、到馆/线上、活动、覆盖状态、承诺、证据缺口、
  当时的资源背景。
- `comparison_limits` 明示不能直接横向比较的原因（线上打开 vs 到馆、代借、
  覆盖缺口、设施/授权资料未到达）。
- 可选资料 `facilities.json`、`authorizations.json`、`vehicles.json`、
  `households.json`、`items.json` 缺失时只标注"未到达"，不合成默认值。
  设施资料到达后会细化无障碍判断；记录为不可用的设备才形成缺口。

## 四、流动资源安排 `analyze_routes`

对每条路线按乡镇给出 `add` / `replace` / `keep` / `defer`：

- 已知需求下界 > 车辆/书车容量 → 提议路线建议替换；
- 乡镇需要无障碍服务但路线未标明设备 → 建议替换；
- 停靠时段落在学校合作时段之外 → 建议替换；
- `confirmed` / `published` / `completed` 路线不因新排名被静默替换，
  输出 `protected=True` 并保留当时依据。

冲突检查：

- 同一车辆或司机在同一时段被两条路线占用 → `resource_double_book`；
- 乡镇被重复安排 → `town_double_book`；
- 路线停靠与活动时间重叠 → `route_program_conflict`；
- 时段资料不完整时标 `unverified=True`，不假定无冲突。

资源冲突会附上"放弃其中一条"的各乡镇服务缺口
（`service_gap["dropped_route_gaps"]`）：失去的停靠、已确认活动、
无障碍机会和可用替代路线。

`commitment_moves(before, after)` 比较两个资料快照，报告被移动或撤销的
已公布承诺，供人工显式处理。

## 五、追加式服务记录 `ServiceLedger`

用于回答"一次路线调整是否改善对应机会"：

1. `record_commitment(...)`：登记已公布/已确认/已完成承诺（只读保护）；
2. `record_route_change(...)`：记录增/换/留，强制保存 `basis`；
3. `record_follow_up(...)`：在调整生效后统计该乡镇的实际使用；
4. `evaluate_route_adjustment(...)`：回看变更、承诺与后续使用。

只有后续记录完整且非空时才给出 `improved=True/False`；没有后续记录或有人数
缺失时为 `None`，避免把"没看到"解释成"没改善"。

## 六、命令行

```bash
PYTHONPATH=src python3 -m library_access_equity --data data --as-of 2026-10-01
PYTHONPATH=src python3 -m library_access_equity --data data --json
```

## 七、测试

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall src tests
```

`tests/test_evidence.py` 覆盖续借、跨馆归还、代借区间、线上访问、缺失人数、
历史时点与搬迁；`tests/test_planning.py` 覆盖容量、无障碍、学校时段、冲突、
承诺移动；`tests/test_ledger.py` 覆盖追加记录与后续使用判定。
