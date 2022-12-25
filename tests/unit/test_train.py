"""Tests train module."""
# pylint: disable=too-many-public-methods,missing-function-docstring
import unittest

import chex
import jax
from absl.testing import flagsaver

from muzero_gojax import main, models, train

FLAGS = main.FLAGS


class TrainCase(chex.TestCase):
    """Tests train module."""

    def setUp(self):
        FLAGS.mark_as_parsed()

    @flagsaver.flagsaver(training_steps=2, board_size=3)
    def test_train_model_changes_params(self):
        rng_key = jax.random.PRNGKey(FLAGS.rng)
        all_models_build_config = models.get_all_models_build_config(
            FLAGS.board_size, FLAGS.dtype)
        go_model, params = models.build_model_with_params(
            all_models_build_config, rng_key)
        new_params, _ = train.train_model(go_model, params, FLAGS.board_size,
                                          FLAGS.dtype, rng_key)
        with self.assertRaises(AssertionError):
            chex.assert_trees_all_equal(params, new_params)

    @flagsaver.flagsaver(training_steps=2, board_size=3, eval_frequency=2)
    def test_train_model_sparse_eval_changes_params(self):
        rng_key = jax.random.PRNGKey(FLAGS.rng)
        all_models_build_config = models.get_all_models_build_config(
            FLAGS.board_size, FLAGS.dtype)
        go_model, params = models.build_model_with_params(
            all_models_build_config, rng_key)
        new_params, _ = train.train_model(go_model, params, FLAGS.board_size,
                                          FLAGS.dtype, rng_key)
        with self.assertRaises(AssertionError):
            chex.assert_trees_all_equal(params, new_params)

    @flagsaver.flagsaver(training_steps=4,
                         board_size=3,
                         update_self_play_policy_frequency=2)
    def test_train_model_sparse_self_play_policy_update_changes_params(self):
        rng_key = jax.random.PRNGKey(FLAGS.rng)
        all_models_build_config = models.get_all_models_build_config(
            FLAGS.board_size, FLAGS.dtype)
        go_model, params = models.build_model_with_params(
            all_models_build_config, rng_key)
        new_params, _ = train.train_model(go_model, params, FLAGS.board_size,
                                          FLAGS.dtype, rng_key)
        with self.assertRaises(AssertionError):
            chex.assert_trees_all_equal(params, new_params)

    @flagsaver.flagsaver(training_steps=1,
                         board_size=3,
                         self_play_model='random')
    def test_train_model_with_random_self_play_noexcept(self):
        rng_key = jax.random.PRNGKey(FLAGS.rng)
        all_models_build_config = models.get_all_models_build_config(
            FLAGS.board_size, FLAGS.dtype)
        go_model, params = models.build_model_with_params(
            all_models_build_config, rng_key)
        train.train_model(go_model, params, FLAGS.board_size, FLAGS.dtype,
                          rng_key)


if __name__ == '__main__':
    unittest.main()
