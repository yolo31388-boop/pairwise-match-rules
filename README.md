# 比赛规则系统

## 项目描述

比赛规则系统的bug修复项目。核心模块 `game/match_rules.py` 存在多个已知bug，需要修复后使测试全部通过。

## 已知问题

- 回合结束不换边不重置装备经济
- 加时赛无胜利条件永远继续
- 平局处理随机选一方赢无规则
- 比赛流程状态转换无互斥无过渡
- 比分追踪只记录回合胜负不实时

## 项目结构

```
├── game/match_rules.py       # 核心模块（带bug）
├── tests/test_match_rules.py    # 验收测试（红态）
├── pyproject.toml          # 项目配置
└── README.md               # 说明文档
```

## 运行测试

```bash
python -m pytest tests/test_match_rules.py -q
```

修复目标：所有 14 个测试全部通过。

## 技术要求

- 纯Python标准库，不引入第三方依赖
- 不修改测试文件
- 保持现有API接口不变
- 修复所有已知bug
