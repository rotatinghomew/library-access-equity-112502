# 图书馆服务公平证据与流动资源安排

这是一个保存合成业务资料的 Python 项目。资料加载层读取原始记录并检查输入契约；分析层在其上提供乡镇服务证据、覆盖缺口、流动资源安排与承诺记录。

资料位于 data/，所有记录都带有合成资料标记。不同文件之间通过稳定标识和时间字段关联，缺失、部分质量和待处理状态都是资料含义的一部分，不能在读取时静默填充。字段说明见 docs/data-dictionary.md，分析口径见 docs/analysis-method.md。

## 主要能力

- `build_report(bundle, as_of=...)`：按截止日期生成乡镇服务证据，人数保留
  下界/未知上界，覆盖状态区分"没有记录"与"没有机会"，并列出证据缺口。
- `analyze_routes(bundle, ...)`：按容量、无障碍设备、学校合作时段、已确认活动
  给出路线增/换/留建议；检测车辆、司机、乡镇和活动时间冲突，并显示放弃某条
  路线造成的服务缺口。已公布路线与到馆日期受保护。
- `commitment_moves(before, after)`：比较资料快照，指出被移动或撤销的已公布承诺。
- `ServiceLedger`：追加式记录承诺、路线调整及调整后的实际使用，可回看一次
  调整是否改善了对应机会。

## 运行

```bash
PYTHONPATH=src python3 -m library_access_equity --data data --as-of 2026-10-01
PYTHONPATH=src python3 -m library_access_equity --data data --json
```

运行测试：python3 -m unittest discover -s tests -v
检查 Python 文件：python3 -m compileall src tests

