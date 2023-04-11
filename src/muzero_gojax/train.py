"""Defines the training graph."""
import functools
from typing import Callable, Optional, Tuple

import chex
import haiku as hk
import jax
import jax.nn
import jax.random
import optax
from absl import flags
from jax import lax

from muzero_gojax import data, game, logger, losses, models

_MODEL_UPDATES_PER_TRAIN_STEP = flags.DEFINE_integer(
    'model_updates_per_train_step', 1,
    'Number of model updates per train step to run.')
_BATCH_SIZE = flags.DEFINE_integer('batch_size', 2,
                                   'Size of the batch to train_model on.')
_TRAJECTORY_LENGTH = flags.DEFINE_integer(
    'trajectory_length', 26, 'Maximum number of game steps for Go.'
    'Usually set to 2(board_size^2).')
_MAX_HYPOTHETICAL_STEPS = flags.DEFINE_integer(
    'max_hypothetical_steps', 1,
    'Maximum number of hypothetical steps to take during training. The number '
    'of hypothetical steps is sampled uniformly from '
    '[1, max_hypothetical_steps].')
SELF_PLAY_SAMPLE_ACTION_SIZE = flags.DEFINE_integer(
    'self_play_sample_action_size', 0,
    'Number of actions to sample for policy improvement during self play.')
PMAP = flags.DEFINE_bool('pmap', False, 'Whether to use pmap for training.')


@chex.dataclass(frozen=True)
class TrainData:
    """Training data."""
    game_stats: game.GameStats  # Sharded
    params: optax.Params  # Replicated
    opt_state: optax.OptState  # Replicated
    loss_metrics: losses.LossMetrics  # Sharded
    rng_key: jax.random.KeyArray  # Sharded


def _update_model(
        grads: optax.Params, optimizer: optax.GradientTransformation,
        params: optax.Params,
        opt_state: optax.OptState) -> Tuple[optax.Params, optax.OptState]:
    """Updates the model in a single train_model step."""
    updates, opt_state = optimizer.update(grads, opt_state, params)
    params = optax.apply_updates(params, updates)
    return params, opt_state


def _update_step(go_model: hk.MultiTransformed,
                 optimizer: optax.GradientTransformation,
                 augmented_trajectories: game.Trajectories, _: int,
                 train_data: TrainData) -> TrainData:
    """Updates the model parameters based on the existing trajectories.

    Args:
        go_model (hk.MultiTransformed): Go model.
        optimizer (optax.GradientTransformation): Optimizer.
        augmented_trajectories (game.Trajectories): Augmented trajectories.
        _ (int): ignored integer index (for multiple model updates).
        train_data (TrainData): Training data.

    Returns:
        TrainData: Training data with updated parmaeters, optimizer state, 
        RNG key and loss metrics.
    """
    logger.log('Tracing update step')
    rng_key, subkey = jax.random.split(train_data.rng_key)
    logger.log('Tracing sample game data')
    game_data: data.GameData = data.sample_game_data(
        augmented_trajectories, subkey, _MAX_HYPOTHETICAL_STEPS.value)
    del subkey
    rng_key, subkey = jax.random.split(rng_key)
    logger.log('Tracing compute loss gradients and metrics')
    grads, loss_metrics = losses.compute_loss_gradients_and_metrics(
        go_model, train_data.params, game_data, subkey)
    if PMAP.value:
        grads = jax.lax.pmean(grads, axis_name='num_devices')
        loss_metrics = jax.lax.pmean(loss_metrics, axis_name='num_devices')
    del subkey
    logger.log('Tracing update model')
    params, opt_state = _update_model(grads, optimizer, train_data.params,
                                      train_data.opt_state)
    return train_data.replace(params=params,
                              opt_state=opt_state,
                              rng_key=rng_key,
                              loss_metrics=loss_metrics)


def _train_step(board_size: int,
                self_play_policy: Optional[models.PolicyModel],
                go_model: hk.MultiTransformed,
                optimizer: optax.GradientTransformation, _: int,
                train_data: TrainData) -> TrainData:
    """
    Executes a single train step comprising self-play, and a model update.
    :param board_size: board size.
    :param self_play_policy: Policy to generate games.
    :param go_model: JAX-Haiku model architecture.
    :param _: ignored training step index.
    :param optimizer: Optax optimizer.
    :param train_data: Train data.
    :return:
    """
    logger.log('Tracing train step...')
    rng_key, subkey = jax.random.split(train_data.rng_key)
    if self_play_policy is None:
        logger.log('Tracing self-play policy model.')
        self_play_policy = models.get_policy_model(
            go_model, train_data.params, SELF_PLAY_SAMPLE_ACTION_SIZE.value)
    logger.log('Tracing self-play.')
    trajectories = game.self_play(
        game.new_trajectories(
            board_size, _BATCH_SIZE.value //
            jax.local_device_count() if PMAP.value else _BATCH_SIZE.value,
            _TRAJECTORY_LENGTH.value), self_play_policy, subkey)
    del subkey
    logger.log('Tracing game stats.')
    game_stats = game.get_game_stats(trajectories)
    if PMAP.value:
        game_stats = jax.lax.pmean(game_stats, axis_name='num_devices')
    logger.log('Tracing trajectory augmentation.')
    augmented_trajectories: game.Trajectories = game.rotationally_augment_trajectories(
        trajectories)
    _, subkey = jax.random.split(rng_key)
    updated_train_data = jax.lax.fori_loop(
        0, _MODEL_UPDATES_PER_TRAIN_STEP.value,
        jax.tree_util.Partial(_update_step, go_model, optimizer,
                              augmented_trajectories),
        train_data.replace(game_stats=game_stats, rng_key=subkey))
    chex.assert_trees_all_equal_shapes(updated_train_data, train_data)
    chex.assert_trees_all_equal_dtypes(updated_train_data, train_data)
    logger.log('Tracing train step done.')
    return updated_train_data


def _multiple_train_steps(board_size: int,
                          self_play_policy: Optional[models.PolicyModel],
                          go_model: hk.MultiTransformed,
                          optimizer: optax.GradientTransformation,
                          num_steps: int, train_data: TrainData) -> TrainData:
    """Executes multiple training steps."""
    # num_steps is marked as a static argument so we can switch between for
    # loops and train steps.
    if num_steps > 1:
        simplified_train_step_fn = jax.tree_util.Partial(
            _train_step, board_size, self_play_policy, go_model, optimizer)
        return lax.fori_loop(0,
                             num_steps,
                             simplified_train_step_fn,
                             init_val=train_data)
    return _train_step(board_size, self_play_policy, go_model, optimizer, 0,
                       train_data)


def get_multi_train_step_fn(
        board_size: int, self_play_policy: Optional[models.PolicyModel],
        go_model: hk.MultiTransformed, optimizer: optax.GradientTransformation,
        num_steps: int) -> Callable[[TrainData], TrainData]:
    """Returns the multi train step function."""
    if PMAP.value:
        return jax.pmap(functools.partial(_multiple_train_steps, board_size,
                                          self_play_policy, go_model,
                                          optimizer, num_steps),
                        axis_name='num_devices',
                        donate_argnums=0)
    return jax.jit(functools.partial(_multiple_train_steps, board_size,
                                     self_play_policy, go_model, optimizer,
                                     num_steps),
                   donate_argnums=0)