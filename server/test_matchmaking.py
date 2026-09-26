import unittest

from matchmaking import MAX_HUMANS, MatchmakingCoordinator, account_id_from_steamid64


def steamid(n: int) -> str:
    return str(76561197960265728 + n)


class DropInMatchmakingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mm = MatchmakingCoordinator(map_pool=["de_dust2"])
        # Register the laptop/Playit endpoint before anyone queues.
        self.mm.server_heartbeat({
            "agent_id": "test-laptop",
            "public_host": "test.example",
            "public_port": 30123,
            "maps": ["de_dust2"],
        })

    def test_first_human_starts_and_late_humans_join_same_match(self) -> None:
        first = steamid(1)
        first_state = self.mm.start(first, game_type=0x02000008, client_version=13881)
        self.assertEqual(first_state["state"], "searching")

        snap = self.mm.snapshot()
        assignment = snap["assignment"]
        self.assertIsNotNone(assignment)
        match_id = assignment["match_id"]
        self.assertEqual(len(assignment["account_ids"]), 1)

        # Pretend srcds returned its native 9106 reservation.
        self.mm.server_heartbeat({
            "agent_id": "test-laptop",
            "public_host": "test.example",
            "public_port": 30123,
            "maps": ["de_dust2"],
            "ready_match_id": match_id,
            "reservation_id": 987654321,
            "reserved_account_ids": [account_id_from_steamid64(first)],
        })
        self.assertEqual(self.mm.state(first)["state"], "reserved")
        self.assertEqual(self.mm.state(first)["server_id"], 0)
        self.assertEqual(self.mm.state(first)["game_type"], 0x02000008)

        # First human enters; the bot-filled game begins.
        self.mm.server_match_started(match_id)
        self.assertEqual(self.mm.state(first)["state"], "in_match")

        second = steamid(2)
        second_state = self.mm.start(second)
        # Do not publish 9107 until srcds has acknowledged the new account in
        # its native reservation.
        self.assertEqual(second_state["state"], "searching")
        self.assertEqual(second_state["match_id"], match_id)

        acknowledged = [
            account_id_from_steamid64(first),
            account_id_from_steamid64(second),
        ]
        self.mm.server_heartbeat({
            "agent_id": "test-laptop",
            "public_host": "test.example",
            "public_port": 30123,
            "maps": ["de_dust2"],
            "ready_match_id": match_id,
            "reservation_id": 987654321,
            "reserved_account_ids": acknowledged,
            "started_match_id": match_id,
        })
        second_state = self.mm.state(second)
        self.assertEqual(second_state["state"], "in_match")
        self.assertEqual(second_state["reservation_id"], 987654321)

        # Fill all ten human slots by queueing later. Each new human stays in
        # searching until the native reservation acknowledges that account.
        for i in range(3, MAX_HUMANS + 1):
            sid = steamid(i)
            state = self.mm.start(sid)
            self.assertEqual(state["state"], "searching")
            acknowledged.append(account_id_from_steamid64(sid))
            self.mm.server_heartbeat({
                "agent_id": "test-laptop",
                "public_host": "test.example",
                "public_port": 30123,
                "maps": ["de_dust2"],
                "ready_match_id": match_id,
                "reservation_id": 987654321,
                "reserved_account_ids": acknowledged,
                "started_match_id": match_id,
            })
            state = self.mm.state(sid)
            self.assertEqual(state["state"], "in_match")
            self.assertEqual(state["match_id"], match_id)

        snap = self.mm.snapshot()
        self.assertEqual(snap["matches"][str(match_id)]["human_count"], MAX_HUMANS)
        self.assertFalse(snap["server_available"])

        # Human 11 waits because the live 10-slot server is genuinely full.
        waiting = steamid(MAX_HUMANS + 1)
        waiting_state = self.mm.start(waiting)
        self.assertEqual(waiting_state["state"], "searching")

        # Once the live match ends, that waiting human immediately allocates
        # the next bot-filled match without waiting for nine more people.
        self.mm.server_match_ended(match_id, {"reason": "game_over"})
        next_state = self.mm.state(waiting)
        self.assertEqual(next_state["state"], "searching")
        self.assertNotEqual(next_state["match_id"], match_id)

    def test_same_player_can_recover_from_stale_search_state(self) -> None:
        first = steamid(42)
        self.mm.start(first, game_type=0x02000008, client_version=13881)
        assignment = self.mm.snapshot()["assignment"]
        self.assertIsNotNone(assignment)
        match_id = assignment["match_id"]

        aid = account_id_from_steamid64(first)
        self.mm.server_heartbeat({
            "agent_id": "test-laptop",
            "public_host": "test.example",
            "public_port": 30123,
            "maps": ["de_dust2"],
            "ready_match_id": match_id,
            "reservation_id": 987654321,
            "reserved_account_ids": [aid],
        })
        self.assertEqual(self.mm.state(first)["state"], "reserved")

        # Reproduce the stale state left by the older coordinator: the player
        # is still attached to the active match, but its public state says
        # searching and there is no queue entry.
        self.mm._states[first] = {
            "state": "searching",
            "server_online": True,
            "server_available": True,
        }

        repaired = self.mm.start(
            first, game_type=0x02000008, client_version=13881
        )
        self.assertEqual(repaired["state"], "reserved")
        self.assertEqual(repaired["match_id"], match_id)
        self.assertEqual(repaired["reservation_id"], 987654321)


    def test_operation_preferred_map_allocates_requested_curated_map(self) -> None:
        mm = MatchmakingCoordinator(map_pool=["de_dust2", "de_nuke"])
        mm.server_heartbeat({
            "agent_id": "test-laptop",
            "public_host": "test.example",
            "public_port": 30123,
            "maps": ["de_dust2", "de_nuke"],
        })

        player = steamid(80)
        state = mm.start(player, preferred_map="de_nuke")
        self.assertEqual(state["state"], "searching")

        assignment = mm.snapshot()["assignment"]
        self.assertIsNotNone(assignment)
        self.assertEqual(assignment["map"], "de_nuke")

    def test_operation_player_waits_for_incompatible_live_map(self) -> None:
        mm = MatchmakingCoordinator(map_pool=["de_dust2", "de_nuke"])
        mm.server_heartbeat({
            "agent_id": "test-laptop",
            "public_host": "test.example",
            "public_port": 30123,
            "maps": ["de_dust2", "de_nuke"],
        })

        first = steamid(81)
        mm.start(first, preferred_map="de_dust2")
        first_assignment = mm.snapshot()["assignment"]
        self.assertIsNotNone(first_assignment)
        first_match = first_assignment["match_id"]

        first_aid = account_id_from_steamid64(first)
        mm.server_heartbeat({
            "agent_id": "test-laptop",
            "public_host": "test.example",
            "public_port": 30123,
            "maps": ["de_dust2", "de_nuke"],
            "ready_match_id": first_match,
            "reservation_id": 123456,
            "reserved_account_ids": [first_aid],
        })
        mm.server_match_started(first_match)

        second = steamid(82)
        second_state = mm.start(second, preferred_map="de_nuke")
        self.assertEqual(second_state["state"], "searching")
        self.assertNotEqual(second_state.get("match_id"), first_match)

        mm.server_match_ended(first_match, {"reason": "game_over"})
        next_assignment = mm.snapshot()["assignment"]
        self.assertIsNotNone(next_assignment)
        self.assertEqual(next_assignment["map"], "de_nuke")
        self.assertIn(account_id_from_steamid64(second), next_assignment["account_ids"])

    def test_cancel_reserved_solo_match_allows_clean_new_allocation(self) -> None:
        first = steamid(43)
        self.mm.start(first)
        assignment = self.mm.snapshot()["assignment"]
        self.assertIsNotNone(assignment)
        old_match_id = assignment["match_id"]

        aid = account_id_from_steamid64(first)
        self.mm.server_heartbeat({
            "agent_id": "test-laptop",
            "public_host": "test.example",
            "public_port": 30123,
            "maps": ["de_dust2"],
            "ready_match_id": old_match_id,
            "reservation_id": 123456789,
            "reserved_account_ids": [aid],
        })
        self.assertEqual(self.mm.state(first)["state"], "reserved")

        stopped = self.mm.stop(first)
        self.assertEqual(stopped["state"], "idle")
        snap = self.mm.snapshot()
        self.assertIsNone(snap["assignment"])
        self.assertEqual(snap["matches"][str(old_match_id)]["state"], "complete")

        restarted = self.mm.start(first)
        self.assertEqual(restarted["state"], "searching")
        new_assignment = self.mm.snapshot()["assignment"]
        self.assertIsNotNone(new_assignment)
        self.assertNotEqual(new_assignment["match_id"], old_match_id)


if __name__ == "__main__":
    unittest.main()
