"""Tests game.py."""
# pylint: disable=missing-function-docstring,duplicate-code,too-many-public-methods,unnecessary-lambda
import unittest

import chex
import jax.numpy as jnp
import jax.random
import numpy as np
from absl.testing import flagsaver

import gojax
from muzero_gojax import game, main, models, nt_utils

FLAGS = main.FLAGS


class GameTestCase(chex.TestCase):
    """Tests game.py."""

    def setUp(self):
        self.board_size = 3
        FLAGS(
            f'foo --board_size={self.board_size} --embed_model=LinearConvEmbed '
            '--value_model=LinearConvValue --policy_model=LinearConvPolicy '
            '--transition_model=LinearConvTransition'.split())
        model_build_config = models.get_model_build_config(FLAGS.board_size)
        self.linear_go_model, self.params = models.build_model_with_params(
            model_build_config, jax.random.PRNGKey(FLAGS.rng))

    def test_new_trajectories_shape(self):
        new_trajectories = game.new_trajectories(board_size=self.board_size,
                                                 batch_size=2,
                                                 trajectory_length=9)

        chex.assert_shape(new_trajectories.nt_states, (2, 9, 6, 3, 3))
        chex.assert_shape(new_trajectories.nt_actions, (2, 9))

    def test_new_trajectories_has_zero_like_states(self):
        new_trajectories = game.new_trajectories(board_size=self.board_size,
                                                 batch_size=2,
                                                 trajectory_length=9)

        np.testing.assert_array_equal(
            new_trajectories.nt_states,
            jnp.zeros_like(new_trajectories.nt_states))

    def test_new_trajectories_initial_actions_are_max_value(self):
        new_trajectories = game.new_trajectories(board_size=self.board_size,
                                                 batch_size=2,
                                                 trajectory_length=9)

        np.testing.assert_array_equal(
            new_trajectories.nt_actions,
            jnp.full_like(new_trajectories.nt_actions,
                          fill_value=-1,
                          dtype='uint16'))

    def test_random_self_play_3x3_42rng_matches_golden_trajectory(self):
        policy_model = models.get_policy_model(self.linear_go_model,
                                               self.params)
        trajectories = game.self_play(game.new_trajectories(
            batch_size=1, board_size=3, trajectory_length=3),
                                      policy_model,
                                      rng_key=jax.random.PRNGKey(42))
        expected_nt_states = gojax.decode_states("""
                                                    _ _ _
                                                    _ _ _
                                                    _ _ _
                                                    
                                                    _ B _
                                                    _ _ _
                                                    _ _ _
                                                    TURN=W
                                                    
                                                    _ B _
                                                    _ _ W
                                                    _ _ _
                                                    """)
        expected_nt_states = jnp.reshape(expected_nt_states, (1, 3, 6, 3, 3))

        def _get_nt_states_pretty_string(_nt_states, index=0):
            _pretty_traj_states_str = '\n'.join(
                map(lambda state: gojax.get_string(state), _nt_states[index]))
            return _pretty_traj_states_str

        pretty_traj_states_str = _get_nt_states_pretty_string(
            trajectories.nt_states)
        np.testing.assert_array_equal(trajectories.nt_states,
                                      expected_nt_states,
                                      pretty_traj_states_str)
        np.testing.assert_array_equal(trajectories.nt_actions,
                                      jnp.array([[1, 5, -1]], dtype='uint16'))

    def test_random_5x5_self_play_yields_black_advantage(self):
        policy_model = models.get_policy_model(models.make_random_model(),
                                               params={})
        trajectories = game.self_play(game.new_trajectories(
            batch_size=128, board_size=5, trajectory_length=24),
                                      policy_model,
                                      rng_key=jax.random.PRNGKey(42))

        game_winners = game.get_nt_player_labels(trajectories.nt_states)

        black_winrate = jnp.mean(game_winners[:, ::2] == 1)
        white_winrate = jnp.mean(game_winners[:, 1::2] == 1)
        tie_rate = jnp.mean(game_winners == 0)

        self.assertBetween(black_winrate, 0.45, 0.55)
        self.assertBetween(white_winrate, 0.25, 0.35)
        self.assertBetween(tie_rate, 0.2, 0.5)

    def test_get_labels_on_single_trajectory(self):
        sample_trajectory = gojax.decode_states("""
                                                _ _ _
                                                _ _ _
                                                _ _ _
                                                
                                                _ _ _
                                                _ B _
                                                _ _ _
                                                TURN=W
                                                
                                                _ _ _
                                                _ B _
                                                _ _ _
                                                PASS=T
                                                """)
        sample_trajectory = jnp.reshape(sample_trajectory, (1, 3, 6, 3, 3))
        np.testing.assert_array_equal(
            game.get_nt_player_labels(sample_trajectory), [[1, -1, 1]])

    def test_get_labels_on_states_with_komi(self):
        sample_trajectory = gojax.decode_states("""
                                            _ _ _
                                            _ _ _
                                            _ _ _

                                            _ _ _
                                            _ B _
                                            _ _ _
                                            TURN=W
                                            
                                            _ B _
                                            B W _
                                            _ B _
                                            
                                            _ B _
                                            B X B
                                            _ B _
                                            TURN=W
                                            """)
        np.testing.assert_array_equal(
            game.get_nt_player_labels(
                jnp.reshape(sample_trajectory, (2, 2, 6, 3, 3))),
            [[1, -1], [1, -1]])

    def test_get_labels_on_states_with_multi_kill(self):
        sample_nt_states = gojax.decode_states("""
                                                B B _ 
                                                B W B 
                                                W _ W 
                                                PASS=T
                                                
                                                B B _ 
                                                B _ B 
                                                _ B _ 
                                                TURN=W
                                                """)
        np.testing.assert_array_equal(
            game.get_nt_player_labels(
                jnp.reshape(sample_nt_states, (1, 2, 6, 3, 3))), [[1, -1]])

    def test_get_game_stats_black_win_pct_on_single_trajectory(self):
        nt_states = gojax.decode_states("""
                                        _ _ _
                                        _ _ _
                                        _ _ _
                                        
                                        _ _ _
                                        _ B _
                                        _ _ _
                                        TURN=W
                                        
                                        _ _ _
                                        _ B _
                                        _ _ _
                                        PASS=T
                                        """)
        nt_states = jnp.reshape(nt_states, (1, 3, 6, 3, 3))
        trajectories = game.Trajectories(nt_states=nt_states,
                                         nt_actions=jnp.zeros((1, 3),
                                                              dtype='int32'))
        np.testing.assert_array_equal(
            game.get_game_stats(trajectories).black_win_pct, [1])

    def test_get_game_stats_tie_pct_on_single_trajectory(self):
        nt_states = gojax.decode_states("""
                                        _ _ _
                                        _ _ _
                                        _ _ _
                                        
                                        _ _ _
                                        _ B _
                                        _ _ _
                                        TURN=W
                                        
                                        _ _ _
                                        _ B _
                                        _ _ _
                                        PASS=T
                                        """)
        nt_states = jnp.reshape(nt_states, (1, 3, 6, 3, 3))
        trajectories = game.Trajectories(nt_states=nt_states,
                                         nt_actions=jnp.zeros((1, 3),
                                                              dtype='int32'))
        np.testing.assert_array_equal(
            game.get_game_stats(trajectories).tie_pct, [0])

    def test_get_game_stats_white_win_pct_on_single_trajectory(self):
        nt_states = gojax.decode_states("""
                                        _ _ _
                                        _ _ _
                                        _ _ _
                                        
                                        _ _ _
                                        _ B _
                                        _ _ _
                                        TURN=W
                                        
                                        _ _ _
                                        _ B _
                                        _ _ _
                                        PASS=T
                                        """)
        nt_states = jnp.reshape(nt_states, (1, 3, 6, 3, 3))
        trajectories = game.Trajectories(nt_states=nt_states,
                                         nt_actions=jnp.zeros((1, 3),
                                                              dtype='int32'))
        np.testing.assert_array_equal(
            game.get_game_stats(trajectories).white_win_pct, [0])

    def test_get_game_stats_avg_game_length_on_single_trajectory(self):
        nt_states = gojax.decode_states("""
                                        _ _ _
                                        _ _ _
                                        _ _ _
                                        
                                        _ _ _
                                        _ B _
                                        _ _ _
                                        TURN=W
                                        
                                        _ _ _
                                        _ B _
                                        _ _ _
                                        PASS=T
                                        """)
        nt_states = jnp.reshape(nt_states, (1, 3, 6, 3, 3))
        trajectories = game.Trajectories(nt_states=nt_states,
                                         nt_actions=jnp.zeros((1, 3),
                                                              dtype='int32'))
        np.testing.assert_array_equal(
            game.get_game_stats(trajectories).avg_game_length, [3])

    def test_get_game_stats_zero_piece_collision_rate(self):
        nt_states = gojax.decode_states("""
                                        _ _ _
                                        _ _ _
                                        _ _ _
                                        """)
        nt_states = jnp.reshape(nt_states, (1, 1, 6, 3, 3))
        trajectories = game.Trajectories(nt_states=nt_states,
                                         nt_actions=jnp.array([[4]]))
        np.testing.assert_array_equal(
            game.get_game_stats(trajectories).piece_collision_rate, [0])

    def test_get_game_stats_zero_piece_collision_rate_with_passing(self):
        nt_states = gojax.decode_states("""
                                        B B B
                                        B B B
                                        B B B
                                        """)
        nt_states = jnp.reshape(nt_states, (1, 1, 6, 3, 3))
        trajectories = game.Trajectories(nt_states=nt_states,
                                         nt_actions=jnp.array([[9]]))
        np.testing.assert_array_equal(
            game.get_game_stats(trajectories).piece_collision_rate, [0])

    def test_get_game_stats_piece_collision_detected(self):
        nt_states = gojax.decode_states("""
                                        B B B
                                        B B B
                                        B B B
                                        """)
        nt_states = jnp.reshape(nt_states, (1, 1, 6, 3, 3))
        trajectories = game.Trajectories(nt_states=nt_states,
                                         nt_actions=jnp.array([[4]]))
        np.testing.assert_array_equal(
            game.get_game_stats(trajectories).piece_collision_rate, [1])

    def test_get_game_stats_piece_collision_rate_is_one_ignoring_terminals(
            self):
        nt_states = gojax.decode_states("""
                                        B B B
                                        B B B
                                        B B B

                                        _ _ _
                                        _ _ _
                                        _ _ _
                                        END=T
                                        """)
        nt_states = jnp.reshape(nt_states, (1, 2, 6, 3, 3))
        trajectories = game.Trajectories(nt_states=nt_states,
                                         nt_actions=jnp.array([[4, 0]]))
        np.testing.assert_array_equal(
            game.get_game_stats(trajectories).piece_collision_rate, [1])

    def test_get_game_stats_piece_collision_rate_is_50_pct(self):
        nt_states = gojax.decode_states("""
                                        _ _ _
                                        _ _ _
                                        _ _ _

                                        _ _ _
                                        _ B _
                                        _ _ _
                                        TURN=W
                                        """)

        nt_states = jnp.reshape(nt_states, (1, 2, 6, 3, 3))
        trajectories = game.Trajectories(nt_states=nt_states,
                                         nt_actions=jnp.array([[4, 4]]))
        np.testing.assert_array_equal(
            game.get_game_stats(trajectories).piece_collision_rate, [0.5])

    def test_get_game_stats_pass_rate_is_zero(self):
        nt_states = gojax.decode_states("""
                                        _ _ _
                                        _ _ _
                                        _ _ _
                                        """)

        nt_states = jnp.reshape(nt_states, (1, 1, 6, 3, 3))
        trajectories = game.Trajectories(nt_states=nt_states,
                                         nt_actions=jnp.array([[0]]))
        np.testing.assert_array_equal(
            game.get_game_stats(trajectories).pass_rate, [0])

    def test_get_game_stats_pass_rate_is_one(self):
        nt_states = gojax.decode_states("""
                                        _ _ _
                                        _ _ _
                                        _ _ _
                                        """)

        nt_states = jnp.reshape(nt_states, (1, 1, 6, 3, 3))
        trajectories = game.Trajectories(nt_states=nt_states,
                                         nt_actions=jnp.array([[9]]))
        np.testing.assert_array_equal(
            game.get_game_stats(trajectories).pass_rate, [1])

    def test_get_game_stats_pass_rate_is_one_ignoring_terminal_states(self):
        nt_states = gojax.decode_states("""
                                        _ _ _
                                        _ _ _
                                        _ _ _

                                        _ _ _
                                        _ B _
                                        _ _ _
                                        END=T
                                        """)

        nt_states = jnp.reshape(nt_states, (1, 2, 6, 3, 3))
        trajectories = game.Trajectories(nt_states=nt_states,
                                         nt_actions=jnp.array([[9, 4]]))
        np.testing.assert_array_equal(
            game.get_game_stats(trajectories).pass_rate, [1])

    def test_get_game_stats_pass_rate_is_zero_with_negative_action(self):
        nt_states = gojax.decode_states("""
                                        _ _ _
                                        _ _ _
                                        _ _ _
                                        """)

        nt_states = jnp.reshape(nt_states, (1, 1, 6, 3, 3))
        trajectories = game.Trajectories(nt_states=nt_states,
                                         nt_actions=jnp.array([[-1]]))
        np.testing.assert_array_equal(
            game.get_game_stats(trajectories).pass_rate, [0])

    def test_get_game_stats_pass_rate_is_50_pct(self):
        nt_states = gojax.decode_states("""
                                        _ _ _
                                        _ _ _
                                        _ _ _

                                        _ _ _
                                        _ _ _
                                        _ _ _
                                        PASS=T
                                        """)

        nt_states = jnp.reshape(nt_states, (1, 2, 6, 3, 3))
        trajectories = game.Trajectories(nt_states=nt_states,
                                         nt_actions=jnp.array([[0, 9]]))
        np.testing.assert_array_equal(
            game.get_game_stats(trajectories).pass_rate, [0.5])

    def test_rotationally_augments_four_equal_single_length_trajectories_on_3x3_board(
            self):
        states = gojax.decode_states("""
                                    B _ _
                                    _ _ _
                                    _ _ _
                                    
                                    B _ _
                                    _ _ _
                                    _ _ _
                                    
                                    B _ _
                                    _ _ _
                                    _ _ _
                                    
                                    B _ _
                                    _ _ _
                                    _ _ _
                                    """)
        nt_states = nt_utils.unflatten_first_dim(states, 4, 1)

        expected_rot_aug_states = gojax.decode_states("""
                                                    B _ _
                                                    _ _ _
                                                    _ _ _
                
                                                    _ _ _
                                                    _ _ _
                                                    B _ _
                
                                                    _ _ _
                                                    _ _ _
                                                    _ _ B
                
                                                    _ _ B
                                                    _ _ _
                                                    _ _ _
                                                    """)
        expected_rot_aug_nt_states = nt_utils.unflatten_first_dim(
            expected_rot_aug_states, 4, 1)
        filler_nt_actions = jnp.zeros((4, 1), dtype='uint16')
        rot_traj = game.rotationally_augment_trajectories(
            game.Trajectories(nt_states=nt_states,
                              nt_actions=filler_nt_actions))
        np.testing.assert_array_equal(rot_traj.nt_states,
                                      expected_rot_aug_nt_states)

    def test_rotationally_augments_start_states_are_noops(self):
        states = gojax.new_states(board_size=3, batch_size=4)
        nt_states = nt_utils.unflatten_first_dim(states, 4, 1)

        filler_nt_actions = jnp.zeros((4, 1), dtype='uint16')
        rot_traj = game.rotationally_augment_trajectories(
            game.Trajectories(nt_states=nt_states,
                              nt_actions=filler_nt_actions))
        np.testing.assert_array_equal(rot_traj.nt_states, nt_states)

    def test_rotationally_augment_pass_actions_are_noops(self):
        indicator_actions = jnp.repeat(jnp.array([[[0, 0, 0], [0, 0, 0],
                                                   [0, 0, 0]]]),
                                       axis=0,
                                       repeats=4)
        expected_indicator_actions = jnp.array([[[0, 0, 0], [0, 0, 0],
                                                 [0, 0, 0]],
                                                [[0, 0, 0], [0, 0, 0],
                                                 [0, 0, 0]],
                                                [[0, 0, 0], [0, 0, 0],
                                                 [0, 0, 0]],
                                                [[0, 0, 0], [0, 0, 0],
                                                 [0, 0, 0]]])

        nt_actions = nt_utils.unflatten_first_dim(
            gojax.action_indicator_to_1d(indicator_actions), 4, 1)
        expected_nt_actions = nt_utils.unflatten_first_dim(
            gojax.action_indicator_to_1d(expected_indicator_actions), 4, 1)

        filler_nt_states = nt_utils.unflatten_first_dim(
            gojax.new_states(board_size=3, batch_size=4), 4, 1)
        rot_traj = game.rotationally_augment_trajectories(
            game.Trajectories(nt_states=filler_nt_states,
                              nt_actions=nt_actions))
        np.testing.assert_array_equal(rot_traj.nt_actions, expected_nt_actions)

    def test_rotationally_augments_states_on_4x1_trajectory_with_3x3_board(
            self):
        states = gojax.decode_states("""
                                    B _ _
                                    _ _ _
                                    _ _ _
                                    """)
        nt_states = jnp.repeat(nt_utils.unflatten_first_dim(states, 1, 1),
                               axis=0,
                               repeats=4)

        expected_rot_aug_states = gojax.decode_states("""
                                                    B _ _
                                                    _ _ _
                                                    _ _ _

                                                    _ _ _
                                                    _ _ _
                                                    B _ _

                                                    _ _ _
                                                    _ _ _
                                                    _ _ B

                                                    _ _ B
                                                    _ _ _
                                                    _ _ _
                                                    """)
        expected_rot_aug_nt_states = nt_utils.unflatten_first_dim(
            expected_rot_aug_states, 4, 1)
        filler_nt_actions = jnp.zeros((4, 1), dtype='uint16')
        rot_traj = game.rotationally_augment_trajectories(
            game.Trajectories(nt_states=nt_states,
                              nt_actions=filler_nt_actions))
        np.testing.assert_array_equal(rot_traj.nt_states,
                                      expected_rot_aug_nt_states)

    def test_rotationally_augments_actions_on_4x1_trajectory_with_3x3_board(
            self):
        nt_actions = jnp.zeros((4, 1), dtype='uint16')
        expected_nt_actions = jnp.array([[0], [6], [8], [2]], dtype='uint16')
        filler_nt_states = nt_utils.unflatten_first_dim(
            gojax.new_states(board_size=3, batch_size=4), 4, 1)

        rot_traj = game.rotationally_augment_trajectories(
            game.Trajectories(nt_states=filler_nt_states,
                              nt_actions=nt_actions))

        np.testing.assert_array_equal(rot_traj.nt_actions, expected_nt_actions)

    def test_rotationally_augments_states_on_8x1_trajectory_with_3x3_board(
            self):
        states = gojax.decode_states("""
                                    B _ _
                                    _ _ _
                                    _ _ _
                                    """)
        nt_states = jnp.repeat(nt_utils.unflatten_first_dim(states, 1, 1),
                               axis=0,
                               repeats=8)

        expected_rot_aug_states = gojax.decode_states("""
                                                    B _ _
                                                    _ _ _
                                                    _ _ _
                                                    
                                                    B _ _
                                                    _ _ _
                                                    _ _ _

                                                    _ _ _
                                                    _ _ _
                                                    B _ _
                                                    
                                                    _ _ _
                                                    _ _ _
                                                    B _ _

                                                    _ _ _
                                                    _ _ _
                                                    _ _ B
                                                    
                                                    _ _ _
                                                    _ _ _
                                                    _ _ B

                                                    _ _ B
                                                    _ _ _
                                                    _ _ _
                                                    
                                                    _ _ B
                                                    _ _ _
                                                    _ _ _
                                                    """)
        expected_rot_aug_nt_states = nt_utils.unflatten_first_dim(
            expected_rot_aug_states, 8, 1)
        filler_nt_actions = jnp.zeros((8, 1), dtype='uint16')
        rot_traj = game.rotationally_augment_trajectories(
            game.Trajectories(nt_states=nt_states,
                              nt_actions=filler_nt_actions))
        np.testing.assert_array_equal(rot_traj.nt_states,
                                      expected_rot_aug_nt_states)

    def test_rotationally_augments_actions_on_8x1_trajectory_with_3x3_board(
            self):
        nt_actions = jnp.zeros((8, 1), dtype='uint16')
        expected_nt_actions = jnp.array(
            [[0], [0], [6], [6], [8], [8], [2], [2]], dtype='uint16')
        filler_nt_states = nt_utils.unflatten_first_dim(
            gojax.new_states(board_size=3, batch_size=8), 8, 1)

        rot_traj = game.rotationally_augment_trajectories(
            game.Trajectories(nt_states=filler_nt_states,
                              nt_actions=nt_actions))

        np.testing.assert_array_equal(rot_traj.nt_actions, expected_nt_actions)

    def test_rot_augments_states_consistently_in_same_traj_on_2x2_traj_with_3x3_board(
            self):
        states = gojax.decode_states("""
                                    B _ _
                                    _ _ _
                                    _ _ _
                                    
                                    B W _
                                    _ _ _
                                    _ _ _
                                    
                                    B _ _
                                    _ _ _
                                    _ _ _
                                    
                                    B W _
                                    _ _ _
                                    _ _ _
                                    """)

        nt_states = nt_utils.unflatten_first_dim(states, 2, 2)

        expected_rot_aug_states = gojax.decode_states("""
                                                    B _ _
                                                    _ _ _
                                                    _ _ _
                                                    
                                                    B W _
                                                    _ _ _
                                                    _ _ _
                                                    
                                                    _ _ _
                                                    _ _ _
                                                    B _ _

                                                    _ _ _
                                                    W _ _
                                                    B _ _
                                                    """)
        expected_rot_aug_nt_states = nt_utils.unflatten_first_dim(
            expected_rot_aug_states, 2, 2)
        filler_nt_actions = jnp.zeros((2, 2), dtype='uint16')
        rot_traj = game.rotationally_augment_trajectories(
            game.Trajectories(nt_states=nt_states,
                              nt_actions=filler_nt_actions))
        np.testing.assert_array_equal(rot_traj.nt_states,
                                      expected_rot_aug_nt_states)

    def test_rot_augments_actions_consistently_in_same_traj_on_2x2_traj_with_3x3_board(
            self):
        nt_actions = jnp.zeros((2, 2), dtype='uint16')
        expected_nt_actions = jnp.array([[0, 0], [6, 6]], dtype='uint16')

        filler_nt_states = nt_utils.unflatten_first_dim(
            gojax.new_states(board_size=3, batch_size=4), 2, 2)
        rot_traj = game.rotationally_augment_trajectories(
            game.Trajectories(nt_states=filler_nt_states,
                              nt_actions=nt_actions))
        np.testing.assert_array_equal(rot_traj.nt_actions, expected_nt_actions)

    def test_pit_win_tie_win_sums_to_n_games(self):
        random_model = models.make_random_model()
        random_policy = models.get_policy_model(random_model, params={})

        n_games = 128
        win_a, tie, win_b = game.pit(random_policy,
                                     random_policy,
                                     FLAGS.board_size,
                                     n_games=n_games,
                                     traj_len=26)
        self.assertEqual(win_a + tie + win_b, n_games)

    def test_random_7x7_self_play_game_stats_matches_golden_win_and_tie_pct(
            self):
        random_model = models.make_random_model()
        random_policy = models.get_policy_model(random_model, params={})

        n_games = 1024
        with flagsaver.flagsaver(board_size=7, trajectory_length=99):
            empty_trajectories = game.new_trajectories(
                FLAGS.board_size,
                batch_size=n_games,
                trajectory_length=FLAGS.trajectory_length)
        trajectories = game.self_play(empty_trajectories, random_policy,
                                      jax.random.PRNGKey(42))
        game_stats = game.get_game_stats(trajectories)
        self.assertAlmostEqual(game_stats.black_win_pct, 0.50, delta=0.05)
        self.assertAlmostEqual(game_stats.white_win_pct, 0.30, delta=0.05)
        self.assertAlmostEqual(game_stats.tie_pct, 0.20, delta=0.05)

    def test_random_9x9_self_play_game_stats_matches_golden_win_and_tie_pct(
            self):
        random_model = models.make_random_model()
        random_policy = models.get_policy_model(random_model, params={})

        n_games = 1024
        with flagsaver.flagsaver(board_size=9, trajectory_length=161):
            empty_trajectories = game.new_trajectories(
                FLAGS.board_size,
                batch_size=n_games,
                trajectory_length=FLAGS.trajectory_length)
        trajectories = game.self_play(empty_trajectories, random_policy,
                                      jax.random.PRNGKey(42))
        game_stats = game.get_game_stats(trajectories)
        self.assertAlmostEqual(game_stats.black_win_pct, 0.50, delta=0.05)
        self.assertAlmostEqual(game_stats.white_win_pct, 0.30, delta=0.05)
        self.assertAlmostEqual(game_stats.tie_pct, 0.20, delta=0.05)

    def test_random_self_play_has_37_25_37_win_tie_win_distribution(self):
        random_model = models.make_random_model()
        random_policy = models.get_policy_model(random_model, params={})

        n_games = 1024
        wins_a, ties, wins_b = game.pit(random_policy,
                                        random_policy,
                                        FLAGS.board_size,
                                        n_games=n_games,
                                        traj_len=FLAGS.trajectory_length,
                                        rng_key=jax.random.PRNGKey(42))
        self.assertAlmostEqual(wins_a / n_games, 0.36, delta=0.02)
        self.assertAlmostEqual(ties / n_games, 0.25, delta=0.02)
        self.assertAlmostEqual(wins_b / n_games, 0.38, delta=0.02)

    def test_random_models_have_similar_win_rate(self):
        random_model = models.make_random_model()
        random_policy = models.get_policy_model(random_model, params={})

        n_games = 4096
        win_a, _, win_b = game.pit(random_policy,
                                   random_policy,
                                   FLAGS.board_size,
                                   n_games=n_games,
                                   traj_len=26)
        self.assertAlmostEqual(win_a / n_games, win_b / n_games, delta=0.03)

    def test_tromp_taylor_has_80_pct_winrate_against_random(self):
        random_model = models.make_random_model()
        random_policy = models.get_policy_model(random_model, params={})

        with flagsaver.flagsaver(embed_model='IdentityEmbed',
                                 value_model='RandomValue',
                                 policy_model='TrompTaylorPolicy',
                                 transition_model='RandomTransition',
                                 area_model='RandomArea',
                                 board_size=5):
            model_build_config = models.get_model_build_config(
                FLAGS.board_size)
            tromp_taylor_model, tromp_taylor_params = models.build_model_with_params(
                model_build_config, jax.random.PRNGKey(FLAGS.rng))
            tromp_taylor_policy = models.get_policy_model(
                tromp_taylor_model, tromp_taylor_params)

        win_a, _, _ = game.pit(tromp_taylor_policy,
                               random_policy,
                               FLAGS.board_size,
                               n_games=128,
                               traj_len=26)
        self.assertAlmostEqual(win_a / 128, 0.80, delta=0.05)

    def test_tromp_taylor_amplified_has_70_pct_winrate_against_tromp_taylor(
            self):
        with flagsaver.flagsaver(embed_model='IdentityEmbed',
                                 value_model='RandomValue',
                                 policy_model='TrompTaylorAmplifiedPolicy',
                                 transition_model='RandomTransition',
                                 area_model='RandomArea',
                                 board_size=5):
            model_build_config = models.get_model_build_config(
                FLAGS.board_size)
            tta_model, tta_params = models.build_model_with_params(
                model_build_config, jax.random.PRNGKey(FLAGS.rng))
            tta_policy = models.get_policy_model(tta_model, tta_params)

        with flagsaver.flagsaver(embed_model='IdentityEmbed',
                                 value_model='RandomValue',
                                 policy_model='TrompTaylorPolicy',
                                 transition_model='RandomTransition',
                                 area_model='RandomArea',
                                 board_size=5):
            model_build_config = models.get_model_build_config(
                FLAGS.board_size)
            tt_model, tt_params = models.build_model_with_params(
                model_build_config, jax.random.PRNGKey(FLAGS.rng))
            tt_policy = models.get_policy_model(tt_model, tt_params)

        win_a, _, _ = game.pit(tta_policy,
                               tt_policy,
                               FLAGS.board_size,
                               n_games=128,
                               traj_len=26,
                               rng_key=jax.random.PRNGKey(42))
        self.assertAlmostEqual(win_a / 128, 0.70, delta=0.05)

    def test_random_has_10_pct_winrate_against_tromp_taylor(self):
        random_model = models.make_random_model()
        random_policy = models.get_policy_model(random_model, params={})

        with flagsaver.flagsaver(embed_model='IdentityEmbed',
                                 value_model='RandomValue',
                                 policy_model='TrompTaylorPolicy',
                                 transition_model='RandomTransition',
                                 area_model='RandomArea',
                                 board_size=5):
            model_build_config = models.get_model_build_config(
                FLAGS.board_size)
            tromp_taylor_model, tromp_taylor_params = models.build_model_with_params(
                model_build_config, jax.random.PRNGKey(FLAGS.rng))
            tromp_taylor_policy = models.get_policy_model(
                tromp_taylor_model, tromp_taylor_params)

        win_a, _, _ = game.pit(random_policy,
                               tromp_taylor_policy,
                               FLAGS.board_size,
                               n_games=128,
                               traj_len=26)
        self.assertAlmostEqual(win_a / 128, 0.10, delta=0.05)

    def test_fully_improved_random_has_60_pct_winrate_against_random(self):
        with flagsaver.flagsaver(board_size=5):
            random_tt_model = models.make_random_policy_tromp_taylor_value_model(
            )
            random_policy = models.get_policy_model(
                random_tt_model,
                params={},
            )
            improved_random_policy = models.get_policy_model(
                random_tt_model, params={}, sample_action_size=2)

            win_a, _, _ = game.pit(improved_random_policy,
                                   random_policy,
                                   FLAGS.board_size,
                                   n_games=1024,
                                   traj_len=26)

        self.assertAlmostEqual(win_a / 1024, 0.60, delta=0.05)

    def test_partially_improved_random_has_60_pct_winrate_against_random(self):
        with flagsaver.flagsaver(board_size=5):
            random_tt_model = models.make_random_policy_tromp_taylor_value_model(
            )
            random_policy = models.get_policy_model(
                random_tt_model,
                params={},
            )
            improved_random_policy = models.get_policy_model(
                random_tt_model, params={}, sample_action_size=2)

            win_a, _, _ = game.pit(improved_random_policy,
                                   random_policy,
                                   FLAGS.board_size,
                                   n_games=1024,
                                   traj_len=26)

        self.assertAlmostEqual(win_a / 1024, 0.60, delta=0.05)

    def test_play_against_model_user_moves_without_fail(self):
        random_model = models.make_random_model()
        random_policy = models.get_policy_model(random_model, params={})
        game.play_against_model(random_policy,
                                board_size=5,
                                input_fn=lambda _: '2 C')

    def test_play_against_model_user_passes_without_fail(self):
        random_model = models.make_random_model()
        random_policy = models.get_policy_model(random_model, params={})
        game.play_against_model(random_policy,
                                board_size=5,
                                input_fn=lambda _: 'pass')

    def test_play_against_model_value_model_runs_noexcept(self):
        random_model = models.make_random_model()
        random_policy = models.get_policy_model(random_model, params={})
        game.play_against_model(random_policy,
                                board_size=5,
                                input_fn=lambda _: 'pass',
                                value_model=models.get_value_model(
                                    random_model, params={}))

    def test_play_against_model_user_exits_without_fail(self):
        random_model = models.make_random_model()
        random_policy = models.get_policy_model(random_model, params={})
        game.play_against_model(random_policy,
                                board_size=5,
                                input_fn=lambda _: 'quit')

    def test_estimate_elo_rating_returns_1400_from_one_win_against_1000(self):
        self.assertAlmostEqual(
            game.estimate_elo_rating(opponent_elo=1000,
                                     wins=1,
                                     ties=0,
                                     losses=0), 1400)

    def test_estimate_elo_rating_returns_1200_from_one_win_one_tie_against_1000(
            self):
        self.assertAlmostEqual(
            game.estimate_elo_rating(opponent_elo=1000,
                                     wins=1,
                                     ties=1,
                                     losses=0), 1200)

    def test_estimate_elo_rating_returns_1000_from_one_win_one_loss_against_1000(
            self):
        self.assertAlmostEqual(
            game.estimate_elo_rating(opponent_elo=1000,
                                     wins=1,
                                     ties=0,
                                     losses=1), 1000)

    def test_estimate_elo_rating_returns_600_from_one_loss_against_1000(self):
        self.assertAlmostEqual(
            game.estimate_elo_rating(opponent_elo=1000,
                                     wins=0,
                                     ties=0,
                                     losses=1), 600)

    def test_estimate_elo_rating_returns_1400_from_one_loss_against_1000(self):
        self.assertAlmostEqual(
            game.estimate_elo_rating(opponent_elo=1000,
                                     wins=2,
                                     ties=0,
                                     losses=0), 1400)


if __name__ == '__main__':
    unittest.main()