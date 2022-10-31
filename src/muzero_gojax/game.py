"""Manages the model generation of Go games."""
from typing import Tuple

import gojax
import haiku as hk
import jax.nn
import jax.random
import jax.tree_util
import optax
import chex
from absl import flags
from jax import lax
from jax import numpy as jnp

from muzero_gojax import models
from muzero_gojax import nt_utils
from muzero_gojax import data

FLAGS = flags.FLAGS


def sample_actions_and_next_states(
        go_model: hk.MultiTransformed, params: optax.Params,
        rng_key: jax.random.KeyArray,
        states: jnp.ndarray) -> Tuple[jnp.ndarray, jnp.ndarray]:
    """
    Simulates the next states of the Go game played out by the given model.

    :param go_model: a model function that takes in a batch of Go states and parameters and
    outputs a batch of action
    probabilities for each state.
    :param params: the model parameters.
    :param rng_key: RNG key used to seed the randomness of the simulation.
    :param states: a batch array of N Go games.
    :return: an N-dimensional integer vector and a N x C x B x B boolean array of Go games.
    """
    embed_model = go_model.apply[models.EMBED_INDEX]
    policy_model = go_model.apply[models.POLICY_INDEX]
    logits = policy_model(params, rng_key, embed_model(params, rng_key,
                                                       states))
    actions = jax.random.categorical(rng_key, logits).astype('uint16')
    return actions, gojax.next_states(states, actions)


def new_trajectories(board_size: int, batch_size: int,
                     trajectory_length: int) -> data.Trajectories:
    """
    Creates an empty array of Go game trajectories.

    :param board_size: B.
    :param batch_size: N.
    :param trajectory_length: T.
    :return: an N x T x C x B x B boolean array, where the third dimension (C) contains
    information about the Go game
    state.
    """
    empty_trajectories = jnp.repeat(
        jnp.expand_dims(gojax.new_states(board_size, batch_size), axis=1),
        trajectory_length, 1)
    return data.Trajectories(nt_states=empty_trajectories,
                             nt_actions=jnp.full(
                                 (batch_size, trajectory_length),
                                 fill_value=-1,
                                 dtype='uint16'))


def update_trajectories(go_model: hk.MultiTransformed, params: optax.Params,
                        rng_key: jax.random.KeyArray, step: int,
                        trajectories: data.Trajectories) -> data.Trajectories:
    """
    Updates the trajectory array for time step `step + 1`.

    :param go_model: a model function that takes in a batch of Go states and parameters and
    outputs a batch of action
    probabilities for each state.
    :param params: the model parameters.
    :param rng_key: RNG key which is salted by the time step.
    :param step: the current time step of the trajectory.
    :param trajectories: A dictionary containing
      * nt_states: an N x T x C x B x B boolean array
      * nt_actions: an N x T integer array
    :return: an N x T x C x B x B boolean array
    """
    actions, next_states = sample_actions_and_next_states(
        go_model, params, jax.random.fold_in(rng_key, step),
        trajectories.nt_states[:, step])
    trajectories = trajectories.replace(
        nt_actions=trajectories.nt_actions.at[:, step].set(actions))
    trajectories = trajectories.replace(
        nt_states=trajectories.nt_states.at[:, step + 1].set(next_states))
    return trajectories


def self_play(empty_trajectories: data.Trajectories,
              go_model: hk.MultiTransformed, params: optax.Params,
              rng_key: jax.random.KeyArray) -> data.Trajectories:
    """
    Simulates a batch of trajectories made from playing the model against itself.

    :param empty_trajectories: Empty trajectories to fill.
    :param go_model: a model function that takes in a batch of Go states and parameters and
    outputs a batch of action
    probabilities for each state.
    :param params: the model parameters.
    :param rng_key: RNG key used to seed the randomness of the self play.
    :return: an N x T x C x B x B boolean array.
    """
    # We iterate trajectory_length - 1 times because we start updating the second column of the
    # trajectories array, not the first.
    return lax.fori_loop(
        0, empty_trajectories.nt_states.shape[1] - 1,
        jax.tree_util.Partial(update_trajectories, go_model, params, rng_key),
        empty_trajectories)


def get_winners(nt_states: jnp.ndarray) -> jnp.ndarray:
    """
    Gets the winner for each trajectory.

    1 = black won
    0 = tie
    -1 = white won

    :param nt_states: an N x T x C x B x B boolean array.
    :return: a boolean array of length N.
    """
    return gojax.compute_winning(nt_states[:, -1])


def get_labels(nt_states: jnp.ndarray) -> jnp.ndarray:
    """
    Game winners from the trajectories.

    The label ({-1, 0, 1}) for the corresponding state represents the winner of the outcome of
    that state's trajectory.

    :param nt_states: An N x T x C x B x B boolean array of trajectory states.
    :return: An N x T integer {-1, 0, 1} array representing whether the player whose turn it is on
    the corresponding state ended up winning, tying, or losing. The last action is undefined and has
    no meaning because it is associated with the last state where no action was taken.
    """
    batch_size, num_steps = nt_states.shape[:2]
    ones = jnp.ones((batch_size, num_steps), dtype='int8')
    white_perspective_negation = ones.at[:, 1::2].set(-1)
    return white_perspective_negation * jnp.expand_dims(
        get_winners(nt_states), 1)


def rotationally_augment_trajectories(
        trajectories: data.Trajectories) -> data.Trajectories:
    """
    Divides the batch (0) dimension into four segments and rotates each
    section 90 degrees counter-clockwise times their section index.

    :param trajectories:
    :return: rotationally augmented trajectories.
    """
    nt_states = trajectories.nt_states
    batch_size, trajectory_length = nt_states.shape[:2]
    nrows, ncols = nt_states.shape[-2:]
    nt_indicator_actions = nt_utils.unflatten_first_dim(
        gojax.action_1d_to_indicator(
            nt_utils.flatten_first_two_dims(trajectories.nt_actions), nrows,
            ncols), batch_size, trajectory_length)
    group_size = max(len(nt_states) // 4, 1)
    nt_aug_states = nt_states
    nt_aug_indic_actions = nt_indicator_actions
    for i in range(1, 4):
        if i >= len(nt_states):
            break
        sliced_augmented_states = jnp.rot90(
            nt_aug_states[i * group_size:(i + 1) * group_size],
            k=i,
            axes=(3, 4))
        sliced_augmented_actions = jnp.rot90(
            nt_aug_indic_actions[i * group_size:(i + 1) * group_size],
            k=i,
            axes=(2, 3))
        nt_aug_states = nt_aug_states.at[i * group_size:(i + 1) *
                                         group_size].set(
                                             sliced_augmented_states)
        nt_aug_indic_actions = nt_aug_indic_actions.at[i * group_size:(
            i + 1) * group_size].set(sliced_augmented_actions)

    nt_aug_actions = nt_utils.unflatten_first_dim(
        gojax.action_indicator_to_1d(
            nt_utils.flatten_first_two_dims(nt_aug_indic_actions)), batch_size,
        trajectory_length)
    return trajectories.replace(nt_states=nt_aug_states,
                                nt_actions=nt_aug_actions)
