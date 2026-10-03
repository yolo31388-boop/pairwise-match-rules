"""
比赛规则系统 - 验收测试

运行方式：python -m pytest tests/test_match_rules.py -q
共 14 个测试用例
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from game.match_rules import MatchRules


def make_system(**overrides):
    """构造一个便于测试的小赛制规则系统"""
    config = {
        "rounds_to_win": 3,
        "max_rounds": 4,
        "overtime_rounds_to_win": 2,
        "overtime_max_rounds": 4,
        "starting_money": 800,
        "overtime_money": 10000,
        "round_time": 100.0,
        "transition_time": 5.0,
    }
    config.update(overrides)
    return MatchRules(config)


def tick(system, seconds=None):
    """推进一个过渡倒计时"""
    system.update(seconds if seconds is not None else system.config["transition_time"] + 0.01)


def go_live(system):
    """开始比赛并进入回合进行中状态"""
    assert system.start_match()
    tick(system)
    assert system.phase == MatchRules.PHASE_ROUND_LIVE


def play_round(system, winner):
    """打完一个回合并推进到下一回合进行中（或比赛结束）"""
    assert system.phase == MatchRules.PHASE_ROUND_LIVE
    assert system.round_end(winner)
    for _ in range(2):
        if system.phase in (MatchRules.PHASE_ROUND_END, MatchRules.PHASE_COUNTDOWN):
            tick(system)


def play_to_overtime(system):
    """按 2:2 打完常规赛，触发平局进入加时"""
    go_live(system)
    for winner in ("A", "B", "A", "B"):
        play_round(system, winner)
    assert system.overtime


class TestMatchRules:
    """比赛规则系统测试套件"""

    def setup_method(self):
        """每个测试前的初始化"""
        self.system = MatchRules()

    def test_case_01(self):
        """回合结束后正确换边：进攻方与防守方互换"""
        system = self.system
        go_live(system)
        assert system.sides == {"A": "attack", "B": "defense"}
        system.round_end("A")
        assert system.sides == {"A": "defense", "B": "attack"}
        tick(system)
        tick(system)
        system.round_end("B")
        assert system.sides == {"A": "attack", "B": "defense"}

    def test_case_02(self):
        """回合结束后重置装备：不能带着上一回合的装备打下一回合"""
        system = self.system
        system.add_player("p1", "A")
        go_live(system)
        assert system.give_equipment("p1", "rifle", cost=300)
        assert system.players["p1"]["equipment"] == ["rifle"]
        system.round_end("A")
        assert system.players["p1"]["equipment"] == []

    def test_case_03(self):
        """回合结束后重置经济：金钱恢复到回合起始金额"""
        system = self.system
        system.add_player("p1", "A")
        go_live(system)
        assert system.give_equipment("p1", "rifle", cost=300)
        assert system.players["p1"]["money"] == 500
        system.round_end("A")
        assert system.players["p1"]["money"] == system.config["starting_money"]

    def test_case_04(self):
        """加时赛有胜利条件：先拿到加时胜场的一方获胜，比赛结束"""
        system = make_system()
        play_to_overtime(system)
        play_round(system, "A")
        assert system.phase == MatchRules.PHASE_ROUND_LIVE
        play_round(system, "A")
        assert system.phase == MatchRules.PHASE_MATCH_END
        assert system.winner == "A"

    def test_case_05(self):
        """加时赛经济重置：进入加时时金钱重置为加时起始金额"""
        system = make_system()
        system.add_player("p1", "A")
        system.add_player("p2", "B")
        play_to_overtime(system)
        assert system.players["p1"]["money"] == system.config["overtime_money"]
        assert system.players["p2"]["money"] == system.config["overtime_money"]
        assert system.players["p1"]["equipment"] == []

    def test_case_06(self):
        """加时赛换边：进入加时时双方互换阵营"""
        system = make_system()
        go_live(system)
        for winner in ("A", "B", "A"):
            play_round(system, winner)
        sides_before = dict(system.sides)
        play_round(system, "B")
        # 触发平局进入加时，阵营相对加时前互换
        assert system.overtime
        assert system.sides["A"] != sides_before["A"]
        assert system.sides["B"] != sides_before["B"]

    def test_case_07(self):
        """加时赛保证终止：加时轮次用尽后比赛必须结束"""
        system = make_system(overtime_rounds_to_win=3, overtime_max_rounds=4)
        play_to_overtime(system)
        for winner in ("A", "B", "A", "B"):
            play_round(system, winner)
        assert system.phase == MatchRules.PHASE_MATCH_END
        assert system.winner == "draw"

    def test_case_08(self):
        """平局处理触发加时赛：常规赛打满且比分相同时进入加时而非直接结束"""
        system = make_system()
        play_to_overtime(system)
        assert system.winner is None
        assert system.phase != MatchRules.PHASE_MATCH_END
        assert system.get_score() == {"A": 2, "B": 2}

    def test_case_09(self):
        """平局处理检查回合差：比分不同则领先方获胜；1:1 不算平局"""
        # 回合差不为 0：领先方直接获胜，不进入加时
        system = make_system(rounds_to_win=5, max_rounds=4)
        go_live(system)
        for winner in ("A", "A", "A", "B"):
            play_round(system, winner)
        assert system.phase == MatchRules.PHASE_MATCH_END
        assert system.winner == "A"
        assert not system.overtime
        # 1:1 远未打满常规赛，不算平局，比赛继续
        system2 = make_system()
        go_live(system2)
        play_round(system2, "A")
        play_round(system2, "B")
        assert not system2.overtime
        assert system2.winner is None
        assert system2.phase == MatchRules.PHASE_ROUND_LIVE

    def test_case_10(self):
        """比赛流程状态互斥：非法状态转换被拒绝，任意时刻只处于一个状态"""
        system = self.system
        phases = {
            MatchRules.PHASE_IDLE, MatchRules.PHASE_COUNTDOWN,
            MatchRules.PHASE_ROUND_LIVE, MatchRules.PHASE_ROUND_END,
            MatchRules.PHASE_PAUSED, MatchRules.PHASE_MATCH_END,
        }
        assert system.phase in phases
        # 空闲状态不能结束回合、不能暂停
        assert not system.round_end("A")
        assert not system.pause()
        # 倒计时中不能结束回合、不能重复开始比赛
        assert system.start_match()
        assert system.phase == MatchRules.PHASE_COUNTDOWN
        assert not system.round_end("A")
        assert not system.start_match()
        # 进入回合进行中后可以暂停/继续，且继续后状态正确
        tick(system)
        assert system.pause()
        assert system.phase == MatchRules.PHASE_PAUSED
        assert not system.round_end("A")
        assert system.resume()
        assert system.phase == MatchRules.PHASE_ROUND_LIVE
        assert not system.resume()

    def test_case_11(self):
        """比赛流程有过渡：状态转换必须经过倒计时，不能跳变"""
        system = self.system
        assert system.start_match()
        assert system.phase == MatchRules.PHASE_COUNTDOWN
        # 倒计时未结束不能进入回合
        system.update(system.config["transition_time"] - 0.1)
        assert system.phase == MatchRules.PHASE_COUNTDOWN
        system.update(0.2)
        assert system.phase == MatchRules.PHASE_ROUND_LIVE
        # 回合结束后先进入结算过渡，而不是直接开始下一回合
        system.round_end("A")
        assert system.phase == MatchRules.PHASE_ROUND_END
        tick(system)
        assert system.phase == MatchRules.PHASE_COUNTDOWN
        tick(system)
        assert system.phase == MatchRules.PHASE_ROUND_LIVE

    def test_case_12(self):
        """比赛暂停后计时器冻结，继续后正常走动"""
        system = self.system
        go_live(system)
        system.update(10.0)
        timer_before = system.round_timer
        assert system.pause()
        system.update(50.0)
        assert system.round_timer == timer_before
        assert system.resume()
        system.update(5.0)
        assert system.round_timer == timer_before - 5.0

    def test_case_13(self):
        """比分追踪实时且记录详细数据：击杀/死亡/伤害立即更新"""
        system = self.system
        system.add_player("p1", "A")
        system.add_player("p2", "B")
        go_live(system)
        assert system.record_kill("p1", "p2", damage=100)
        assert system.record_damage("p1", 50)
        # 不等回合结束，数据立即可查
        assert system.get_player_stats("p1") == {"kills": 1, "deaths": 0, "damage": 150}
        assert system.get_player_stats("p2") == {"kills": 0, "deaths": 1, "damage": 0}
        assert system.get_player_stats("p3") is None

    def test_case_14(self):
        """比分有上限：达到胜利比分比赛立即结束，之后拒绝继续记分"""
        system = make_system(rounds_to_win=2, max_rounds=30)
        go_live(system)
        play_round(system, "A")
        assert system.phase == MatchRules.PHASE_ROUND_LIVE
        play_round(system, "A")
        assert system.phase == MatchRules.PHASE_MATCH_END
        assert system.winner == "A"
        assert system.get_score() == {"A": 2, "B": 0}
        assert system.get_score()["A"] <= system.score_cap
        # 比赛结束后不能再记分，比分不会被刷上去
        assert not system.round_end("A")
        assert system.get_score() == {"A": 2, "B": 0}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
