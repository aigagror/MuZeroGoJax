"""Tests model.py."""
# pylint: disable=missing-function-docstring,no-self-use,unnecessary-lambda,duplicate-code
import unittest

import chex
import gojax
import haiku as hk
import jax
import jax.numpy as jnp
import numpy as np
from absl.testing import parameterized

from muzero_gojax import main
from muzero_gojax import models


class OutputShapeTestCase(chex.TestCase):
    """Tests the output shape of models."""

    @parameterized.named_parameters(('black_cnn_lite', models.embed.BlackCNNLite, 32, (2, 32, 3, 3)),
        ('cnn_intermediate', models.embed.CNNIntermediateEmbed, 256, (2, 256, 3, 3)),
        ('black_cnn_intermediate', models.embed.BlackCNNIntermediate, 256, (2, 256, 3, 3)),
        ('black_real_perspective', models.transition.BlackRealTransition, None, (2, 10, gojax.NUM_CHANNELS, 3, 3)),
        ('cnn_lite_transition', models.transition.CNNLiteTransition, 32, (2, 10, 32, 3, 3)),
        ('cnn_intermediate_transition', models.transition.CNNIntermediateTransition, 256, (2, 10, 256, 3, 3)),
        ('cnn_lite_policy', models.policy.CNNLitePolicy, 6, (2, 10)), )
    def test_from_two_states_(self, model_class, hdim, expected_shape):
        board_size = 3
        model = hk.without_apply_rng(hk.transform(lambda x: model_class(board_size, hdim)(x)))
        states = gojax.new_states(batch_size=2, board_size=board_size)
        params = model.init(jax.random.PRNGKey(42), states)
        output = model.apply(params, states)
        chex.assert_shape(output, expected_shape)


class EmbedModelTestCase(chex.TestCase):
    """Tests embed models."""

    def test_black_perspective(self):
        states = gojax.decode_states("""
                    B _ _
                    W _ _
                    _ _ _
                    TURN=B
                    
                    _ _ _
                    _ B _
                    _ W _
                    TURN=W
                    """)
        expected_embedding = gojax.decode_states("""
                    B _ _
                    W _ _
                    _ _ _
                    TURN=B
         
                    _ _ _
                    _ W _
                    _ B _
                    TURN=B
                    """)
        embed_model = hk.without_apply_rng(
            hk.transform(lambda x: models.embed.BlackPerspective(board_size=3, hdim=None)(x)))
        rng = jax.random.PRNGKey(42)
        params = embed_model.init(rng, states)
        self.assertEmpty(params)
        np.testing.assert_array_equal(embed_model.apply(params, states), expected_embedding)


class TransitionTestCase(chex.TestCase):
    """Tests the transition models."""

    def test_get_real_transition_model_output(self):
        board_size = 3
        main.FLAGS(f'foo --board_size={board_size} --embed_model=identity --value_model=linear '
                   '--policy_model=linear --transition_model=real'.split())
        go_model = hk.without_apply_rng(models.make_model(main.FLAGS))
        new_states = gojax.new_states(batch_size=1, board_size=board_size)
        params = go_model.init(jax.random.PRNGKey(42), new_states)

        transition_model = go_model.apply[3]
        transition_output = transition_model(params, new_states)
        expected_transition = jnp.expand_dims(gojax.decode_states("""
                              B _ _
                              _ _ _
                              _ _ _

                              _ B _
                              _ _ _
                              _ _ _

                              _ _ B
                              _ _ _
                              _ _ _

                              _ _ _
                              B _ _
                              _ _ _

                              _ _ _
                              _ B _
                              _ _ _

                              _ _ _
                              _ _ B
                              _ _ _

                              _ _ _
                              _ _ _
                              B _ _

                              _ _ _
                              _ _ _
                              _ B _

                              _ _ _
                              _ _ _
                              _ _ B
                              
                              _ _ _
                              _ _ _
                              _ _ _
                              PASS=T
                              """, turn=gojax.WHITES_TURN), axis=0)
        np.testing.assert_array_equal(transition_output, expected_transition)


class ValueTestCase(chex.TestCase):
    """Tests the value models."""

    def test_tromp_taylor_value_model_output(self):
        states = gojax.decode_states("""
                                    _ B B
                                    _ W _
                                    _ _ _
                                    TURN=B
                                    
                                    _ W _
                                    _ _ _
                                    _ _ _
                                    TURN=W
                                    """)
        tromp_taylor_value = hk.without_apply_rng(
            hk.transform(lambda x: models.value.TrompTaylorValue(board_size=3, hdim=None)(x)))
        params = tromp_taylor_value.init(None, states)
        self.assertEmpty(params)
        np.testing.assert_array_equal(tromp_taylor_value.apply(params, states), [1, 9])


class PolicyTestCase(chex.TestCase):
    """Tests the policy models."""

    def test_tromp_taylor_policy_model_output(self):
        states = gojax.decode_states("""
                                    _ B B
                                    _ W _
                                    _ _ _
                                    TURN=B

                                    _ W _
                                    _ _ _
                                    _ _ _
                                    TURN=W
                                    """)
        tromp_taylor_policy = hk.without_apply_rng(
            hk.transform(lambda x: models.policy.TrompTaylorPolicy(board_size=3, hdim=None)(x)))
        params = tromp_taylor_policy.init(None, states)
        self.assertEmpty(params)
        np.testing.assert_array_equal(tromp_taylor_policy.apply(params, states),
                                      [[2, 1, 1, 3, 1, 2, 2, 2, 2, 1], [9, 9, 9, 9, 9, 9, 9, 9, 9, 9]])


class MakeModelTestCase(chex.TestCase):
    """Tests model.py."""

    @parameterized.named_parameters((
            '_random', 'identity', 'random', 'random', 'random', (1, gojax.NUM_CHANNELS, 3, 3), (1,), (1, 10),
            (1, 10, gojax.NUM_CHANNELS, 3, 3)), (
            '_linear', 'identity', 'linear', 'linear', 'linear', (1, gojax.NUM_CHANNELS, 3, 3), (1,), (1, 10),
            (1, 10, gojax.NUM_CHANNELS, 3, 3)), )
    def test_single_batch_board_size_three(self, embed_model_name, value_model_name, policy_model_name,
                                           transition_model_name, expected_embed_shape, expected_value_shape,
                                           expected_policy_shape, expected_transition_shape):
        # pylint: disable=too-many-arguments
        # Build the model
        board_size = 3
        main.FLAGS(f'foo --board_size={board_size} --embed_model={embed_model_name} '
                   f'--value_model={value_model_name} '
                   f'--policy_model={policy_model_name} --transition_'
                   f'model={transition_model_name}'.split())
        go_model = models.make_model(main.FLAGS)
        new_states = gojax.new_states(batch_size=1, board_size=board_size)
        params = go_model.init(jax.random.PRNGKey(42), new_states)
        # Check the shapes
        chex.assert_shape((go_model.apply[0](params, jax.random.PRNGKey(42), new_states),
                           go_model.apply[1](params, jax.random.PRNGKey(42), new_states),
                           go_model.apply[2](params, jax.random.PRNGKey(42), new_states),
                           go_model.apply[3](params, jax.random.PRNGKey(42), new_states)), (
                              expected_embed_shape, expected_value_shape, expected_policy_shape,
                              expected_transition_shape))

    def test_get_random_model_params(self):
        board_size = 3
        main.FLAGS(f'foo --board_size={board_size} --embed_model=identity --value_model=random '
                   '--policy_model=random --transition_model=random'.split())
        go_model = models.make_model(main.FLAGS)
        self.assertIsInstance(go_model, hk.MultiTransformed)
        params = go_model.init(jax.random.PRNGKey(42), gojax.new_states(batch_size=2, board_size=board_size))
        self.assertIsInstance(params, dict)
        self.assertEqual(len(params), 0)

    def test_get_linear_model_params(self):
        board_size = 3
        main.FLAGS(f'foo --board_size={board_size} --embed_model=identity --value_model=linear '
                   '--policy_model=linear --transition_model=linear'.split())
        go_model = models.make_model(main.FLAGS)
        self.assertIsInstance(go_model, hk.MultiTransformed)
        params = go_model.init(jax.random.PRNGKey(42), gojax.new_states(batch_size=2, board_size=board_size))
        self.assertIsInstance(params, dict)
        chex.assert_tree_all_equal_structs(params, {'linear3_d_policy': {'action_w': 0},
                                                    'linear3_d_transition': {'transition_b': 0, 'transition_w': 0},
                                                    'linear3_d_value': {'value_b': 0, 'value_w': 0}})

    def test_get_linear_model_output_zero_params(self):
        board_size = 3
        main.FLAGS(f'foo --board_size={board_size} --embed_model=identity --value_model=linear '
                   '--policy_model=linear --transition_model=linear'.split())
        go_model = hk.without_apply_rng(models.make_model(main.FLAGS))
        new_states = gojax.new_states(batch_size=1, board_size=board_size)
        params = go_model.init(jax.random.PRNGKey(42), new_states)
        params = jax.tree_util.tree_map(lambda p: jnp.zeros_like(p), params)

        ones_like_states = jnp.ones_like(new_states)
        embed_model = go_model.apply[0]
        output = embed_model(params, ones_like_states)
        np.testing.assert_array_equal(output, ones_like_states)

        for sub_model in go_model.apply[1:]:
            output = sub_model(params, ones_like_states)
        np.testing.assert_array_equal(output, jnp.zeros_like(output))

    def test_get_linear_model_output_ones_params(self):
        board_size = 3
        main.FLAGS(f'foo --board_size={board_size} --embed_model=identity --value_model=linear '
                   '--policy_model=linear --transition_model=linear'.split())
        go_model = hk.without_apply_rng(models.make_model(main.FLAGS))
        new_states = gojax.new_states(batch_size=1, board_size=board_size)
        params = go_model.init(jax.random.PRNGKey(42), new_states)
        params = jax.tree_util.tree_map(lambda p: jnp.ones_like(p), params)

        ones_like_states = jnp.ones_like(new_states)
        embed_model, value_model, policy_model, transition_model = go_model.apply
        output = embed_model(params, ones_like_states)
        np.testing.assert_array_equal(output, ones_like_states)

        value_output = value_model(params, ones_like_states)
        np.testing.assert_array_equal(value_output,
                                      jnp.full_like(value_output, gojax.NUM_CHANNELS * board_size ** 2 + 1))
        policy_output = policy_model(params, ones_like_states)
        np.testing.assert_array_equal(policy_output, jnp.full_like(policy_output, gojax.NUM_CHANNELS * board_size ** 2))
        transition_output = transition_model(params, ones_like_states)
        np.testing.assert_array_equal(transition_output,
                                      jnp.full_like(transition_output, gojax.NUM_CHANNELS * board_size ** 2 + 1))

    def test_tromp_taylor_model_runs(self):
        board_size = 3
        main.FLAGS(f'foo --board_size={board_size} --embed_model=identity --value_model=tromp_taylor '
                   '--policy_model=tromp_taylor --transition_model=real'.split())
        go_model = hk.without_apply_rng(models.make_model(main.FLAGS))
        new_states = gojax.new_states(batch_size=1, board_size=board_size)
        params = go_model.init(jax.random.PRNGKey(42), new_states)

        embed_model, value_model, policy_model, transition_model = go_model.apply
        embeds = embed_model(params, new_states)
        np.testing.assert_array_equal(value_model(params, embeds), [0])
        np.testing.assert_array_equal(policy_model(params, embeds), [[9, 9, 9, 9, 9, 9, 9, 9, 9, 0]])
        all_transitions = transition_model(params, embeds)
        chex.assert_shape(all_transitions, (1, 10, 6, 3, 3))
        np.testing.assert_array_equal(value_model(params, all_transitions[:, 0]), [-9])
        np.testing.assert_array_equal(policy_model(params, all_transitions[:, 0]), [[-9, 0, 0, 0, 0, 0, 0, 0, 0, -9]])

    def test_cnn_lite_model_generates_zero_output_on_empty_state(self):
        """It's important that the model can create non-zero output on an all-zero input."""
        board_size = 3
        main.FLAGS(f'foo --board_size={board_size} --embed_model=cnn_lite --value_model=linear '
                   '--policy_model=cnn_lite --transition_model=cnn_lite'.split())
        go_model = models.make_model(main.FLAGS)
        new_states = gojax.new_states(batch_size=1, board_size=board_size)
        rng = jax.random.PRNGKey(42)
        params = go_model.init(rng, new_states)
        embed_model, value_model, policy_model, transition_model = go_model.apply
        embeds = embed_model(params, rng, new_states)
        self.assertEqual(jnp.abs(value_model(params, rng, embeds)), 0)
        self.assertEqual(jnp.var(policy_model(params, rng, embeds)), 0)

    if __name__ == '__main__':
        unittest.main()
