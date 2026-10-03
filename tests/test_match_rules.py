"""
比赛规则系统 - 验收测试

运行方式：python -m pytest tests/test_match_rules.py -q
共 14 个测试用例
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from game.match_rules import (
    MatchRules, Phase, TEAM_A, TEAM_B, ATTACKER, DEFENDER,
)


def make_system(**config):
    system = MatchRules(config or None)
    system.add_player("a1", TEAM_A)
    system.add_player("a2", TEAM_A)
    system.add_player("b1", TEAM_B)
    system.add_player("b2", TEAM_B)
    return system


def play_round(system, winner):
    """打完一整个回合：冻结时间 -> 回合进行 -> 回合结束 -> 下一回合冻结"""
    assert system.phase == Phase.FREEZE_TIME
    system.update(system.config["freeze_time"])
    assert system.phase == Phase.ROUND_IN_PROGRESS
    system.update(1.0)
    system.round_end(winner)
    if system.phase == Phase.MATCH_END:
        return
    system.update(system.config["round_end_time"])


def play_regulation(system, wins_a, wins_b):
    """打完常规赛的回合，wins_a + wins_b 不超过 30"""
    system.start_match()
    winners = [TEAM_A] * wins_a + [TEAM_B] * wins_b
    for winner in winners:
        if system.phase == Phase.MATCH_END:
            break
        play_round(system, winner)


def reach_overtime(system):
    """打到 15:15 并触发加时赛，返回时处于加时第 1 回合冻结时间"""
    play_regulation(system, 15, 15)
    assert system.phase == Phase.FREEZE_TIME
    assert system.overtime_number == 1


class TestMatchRules:
    """比赛规则系统测试套件"""

    def setup_method(self):
        """每个测试前的初始化"""
        self.system = make_system()

    def test_case_01(self):
        """回合结束后换边：进攻方与防守方互换"""
        s = self.system
        s.start_match()
        assert s.sides[TEAM_A] == ATTACKER
        assert s.sides[TEAM_B] == DEFENDER
        s.update(s.config["freeze_time"])
        s.round_end(TEAM_A)
        assert s.sides[TEAM_A] == DEFENDER
        assert s.sides[TEAM_B] == ATTACKER
        s.update(s.config["round_end_time"])
        s.update(s.config["freeze_time"])
        s.round_end(TEAM_B)
        assert s.sides[TEAM_A] == ATTACKER
        assert s.sides[TEAM_B] == DEFENDER

    def test_case_02(self):
        """回合结束后重置装备：上一回合购买的装备不带入下一回合"""
        s = self.system
        s.start_match()
        s.buy("a1", "rifle", 400)
        assert "rifle" in s.players["a1"].equipment
        s.update(s.config["freeze_time"])
        s.round_end(TEAM_A)
        assert s.players["a1"].equipment == []

    def test_case_03(self):
        """回合结束后重置经济：金钱恢复为回合起始经济"""
        s = self.system
        s.start_match()
        s.buy("a1", "rifle", 400)
        assert s.players["a1"].money == s.config["starting_money"] - 400
        s.update(s.config["freeze_time"])
        s.round_end(TEAM_A)
        assert s.players["a1"].money == s.config["starting_money"]

    def test_case_04(self):
        """新回合重置玩家位置"""
        s = self.system
        s.start_match()
        s.update(s.config["freeze_time"])
        s.players["a1"].position = (100.0, 200.0)
        s.round_end(TEAM_A)
        s.update(s.config["round_end_time"])
        assert s.players["a1"].position == (0.0, 0.0)

    def test_case_05(self):
        """加时赛有胜利条件：先拿 4 分且领先 2 分即获胜，不会永远继续"""
        s = self.system
        reach_overtime(s)
        for _ in range(4):
            play_round(s, TEAM_A)
        assert s.phase == Phase.MATCH_END
        assert s.winner == TEAM_A
        assert s.score[TEAM_A] == 19
        assert s.overtime() == "match_over"

    def test_case_06(self):
        """加时赛 3:3 打平：进入下一个加时赛而不是无限循环或随机判胜"""
        s = self.system
        reach_overtime(s)
        for winner in [TEAM_A, TEAM_B, TEAM_A, TEAM_B, TEAM_A, TEAM_B]:
            play_round(s, winner)
        assert s.phase != Phase.MATCH_END
        assert s.overtime_number == 2
        assert s.ot_score == {TEAM_A: 0, TEAM_B: 0}
        assert s.score[TEAM_A] == 18
        assert s.score[TEAM_B] == 18

    def test_case_07(self):
        """加时赛经济重置：进入加时时金钱重置为加时经济，不带入常规赛经济"""
        s = self.system
        play_regulation(s, 14, 14)
        # 第 29 回合冻结时间花光金钱
        s.buy("a1", "rifle", 800)
        assert s.players["a1"].money == 0
        s.update(s.config["freeze_time"])
        s.round_end(TEAM_A)   # 15:14
        s.update(s.config["round_end_time"])
        s.update(s.config["freeze_time"])
        s.round_end(TEAM_B)   # 15:15，进入回合结束过渡
        s.update(s.config["round_end_time"])  # 过渡结束触发加时
        assert s.overtime_number == 1
        for player in s.players.values():
            assert player.money == s.config["overtime_money"]
            assert player.equipment == []

    def test_case_08(self):
        """加时赛换边：进入加时换边，且每个半场（3 回合）后再次换边"""
        s = self.system
        reach_overtime(s)
        sides_at_ot_start = dict(s.sides)
        assert s.sides[TEAM_A] == DEFENDER  # 进入加时后换边
        for _ in range(3):
            play_round(s, TEAM_A)
        assert s.phase != Phase.MATCH_END
        assert s.sides[TEAM_A] == ATTACKER  # 半场后再次换边
        assert s.sides != sides_at_ot_start

    def test_case_09(self):
        """平局处理有规则：15:15 触发加时；回合数未满的平分（如 1:1）不算平局"""
        s = self.system
        # 1:1 不算平局，比赛继续
        play_regulation(s, 1, 1)
        assert s.phase == Phase.FREEZE_TIME
        assert s.round_number == 3
        # 15:15 触发加时而不是随机判胜
        s2 = make_system()
        reach_overtime(s2)
        assert s2.score == {TEAM_A: 15, TEAM_B: 15}
        assert s2.overtime_number == 1
        assert s2.winner is None

    def test_case_10(self):
        """平局处理检查回合差：非平局比分按规则判定胜负，不随机选边"""
        s = self.system
        play_regulation(s, 16, 14)
        assert s.phase == Phase.MATCH_END
        assert s.winner == TEAM_A
        assert s.tie_breaker() == "match_over"
        # 16:14 不应进入加时
        assert s.overtime_number == 0

    def test_case_11(self):
        """比赛流程状态互斥：任意时刻只处于一个状态，非法转换被拒绝"""
        s = self.system
        assert s.phase == Phase.WARMUP
        with pytest.raises(RuntimeError):
            s.round_end(TEAM_A)  # 热身时不能结束回合
        s.start_match()
        assert s.phase == Phase.FREEZE_TIME
        with pytest.raises(RuntimeError):
            s.start_match()  # 不能重复开始比赛
        with pytest.raises(RuntimeError):
            s.round_end(TEAM_A)  # 冻结时间不能结束回合
        s.update(s.config["freeze_time"])
        assert s.phase == Phase.ROUND_IN_PROGRESS
        s.round_end(TEAM_A)
        assert s.phase == Phase.ROUND_END
        with pytest.raises(RuntimeError):
            s.round_end(TEAM_A)  # 不能同时处于回合结束又再次结束回合

    def test_case_12(self):
        """比赛流程有过渡：冻结时间倒计时结束才进入回合，回合结束有过渡期"""
        s = make_system(freeze_time=2.0, round_end_time=1.0)
        s.start_match()
        assert s.phase == Phase.FREEZE_TIME
        s.update(1.0)
        assert s.phase == Phase.FREEZE_TIME  # 倒计时未结束，不直接进回合
        s.update(1.0)
        assert s.phase == Phase.ROUND_IN_PROGRESS
        s.round_end(TEAM_A)
        assert s.phase == Phase.ROUND_END  # 先进入结算过渡，而不是直接下一回合
        s.update(0.5)
        assert s.phase == Phase.ROUND_END
        s.update(0.5)
        assert s.phase == Phase.FREEZE_TIME
        assert s.round_number == 2

    def test_case_13(self):
        """比赛暂停后继续：暂停期间计时器不走，继续后从暂停处恢复"""
        s = make_system(round_time=100.0)
        s.start_match()
        s.update(s.config["freeze_time"])
        s.update(10.0)
        assert s.match_flow()["round_time_remaining"] == pytest.approx(90.0)
        s.pause()
        s.update(50.0)
        assert s.phase == Phase.ROUND_IN_PROGRESS  # 暂停中不因超时结束回合
        assert s.match_flow()["round_time_remaining"] == pytest.approx(90.0)
        s.resume()
        s.update(30.0)
        assert s.match_flow()["round_time_remaining"] == pytest.approx(60.0)

    def test_case_14(self):
        """比分追踪：实时记录击杀/死亡/伤害，比分有上限（先到 16 分结束比赛）"""
        s = self.system
        s.start_match()
        s.update(s.config["freeze_time"])
        # 详细数据实时更新，无需等回合结束
        s.record_kill("a1", "b1")
        s.record_damage("a1", 87.5)
        s.record_kill("a1", "b2")
        s.record_damage("a1", 100.0)
        s.record_kill("b1", "a2")
        tracking = s.score_tracking()
        assert tracking["players"]["a1"]["kills"] == 2
        assert tracking["players"]["a1"]["damage"] == pytest.approx(187.5)
        assert tracking["players"]["b1"]["deaths"] == 1
        assert tracking["players"]["a2"]["deaths"] == 1
        # 比分实时反映在快照中
        s.round_end(TEAM_A)
        assert s.score_tracking()["score"][TEAM_A] == 1
        # 比分上限：先到 16 分比赛即结束，不会出现 100:0 还继续打
        s.update(s.config["round_end_time"])
        for _ in range(15):
            play_round(s, TEAM_A)
        assert s.phase == Phase.MATCH_END
        assert s.winner == TEAM_A
        assert s.score[TEAM_A] == 16
        assert s.score[TEAM_A] <= s.config["win_target"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
