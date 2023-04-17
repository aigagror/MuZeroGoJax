"""Processes and samples data from games for model updates."""

import chex
import jax
import jax.numpy as jnp

import gojax
from muzero_gojax import game, nt_utils


@chex.dataclass(frozen=True)
class GameData:
    """Game data.

    The model is trained to predict the following:
    • The end state given the start state and the actions taken
    • The end reward given the start state and the actions taken
    • The start reward given the start state
    """
    # Start states.
    start_states: jnp.ndarray
    # Actions taken from start state to end state.
    nk_actions: jnp.ndarray
    # End states.
    end_states: jnp.ndarray

    # A 2 x B x B array representing which player owns each point.
    # First index is player, second is opponent.
    start_player_final_areas: jnp.ndarray
    # A 2 x B x B array representing which player owns each point.
    # First index is player, second is opponent.
    end_player_final_areas: jnp.ndarray
    # TODO: Add sampled q-values.


def sample_game_data(trajectories: game.Trajectories,
                     rng_key: jax.random.KeyArray,
                     max_hypo_steps: int) -> GameData:
    """Samples game data from trajectories.

    For each trajectory, we independently sample our hypothetical step value k
    uniformly from [1, max_hypo_steps]. We then sample two states from
    the trajectory. The index of the first state, i, is sampled uniformly from
    all non-terminal states of the trajectory. The index of the second state, j,
    is min(i+k, n) where n is the length of the trajectory 
    (including the terminal state).

    Args:
        trajectories: Trajectories from a game.
        rng_key: Random key for sampling.
        max_hypo_steps: Maximum number of hypothetical steps to use. 
                        Must be at least 1.

    Returns:
        Game data sampled from trajectories.
    """
    if max_hypo_steps < 1:
        raise ValueError('max_hypo_steps must be at least 1.')
    batch_size, traj_len = trajectories.nt_states.shape[:2]
    next_k_indices = jnp.repeat(jnp.expand_dims(jnp.arange(max_hypo_steps),
                                                axis=0),
                                batch_size,
                                axis=0)
    batch_order_indices = jnp.arange(batch_size)
    game_ended = nt_utils.unflatten_first_dim(
        gojax.get_ended(nt_utils.flatten_first_two_dims(
            trajectories.nt_states)), batch_size, traj_len)
    base_sample_state_logits = game_ended * float('-inf')
    game_len = jnp.sum(~game_ended, axis=1)
    rng_key, categorical_key = jax.random.split(rng_key)
    start_indices = jax.random.categorical(categorical_key,
                                           base_sample_state_logits,
                                           axis=1)
    del categorical_key
    chex.assert_rank(start_indices, 1)
    _, randint_key = jax.random.split(rng_key)
    unclamped_hypo_steps = jax.random.randint(randint_key,
                                              shape=(batch_size, ),
                                              minval=1,
                                              maxval=max_hypo_steps + 1)
    del randint_key
    chex.assert_equal_shape([start_indices, game_len, unclamped_hypo_steps])
    end_indices = jnp.minimum(start_indices + unclamped_hypo_steps, game_len)
    hypo_steps = jnp.minimum(unclamped_hypo_steps, end_indices - start_indices)
    start_states = trajectories.nt_states[batch_order_indices, start_indices]
    end_states = trajectories.nt_states[batch_order_indices, end_indices]
    first_terminal_states = trajectories.nt_states[batch_order_indices,
                                                   game_len]
    nk_actions = trajectories.nt_actions[
        jnp.expand_dims(batch_order_indices, axis=1),
        jnp.expand_dims(start_indices, axis=1) +
        next_k_indices].astype('int32')
    chex.assert_shape(nk_actions, (batch_size, max_hypo_steps))
    nk_actions = jnp.where(
        next_k_indices < jnp.expand_dims(hypo_steps, axis=1), nk_actions,
        jnp.full_like(nk_actions, fill_value=-1))
    final_areas = gojax.compute_areas(first_terminal_states)
    start_player_final_areas = jnp.where(
        jnp.expand_dims(gojax.get_turns(start_states), (1, 2, 3)),
        final_areas[:, [1, 0]], final_areas)
    end_player_final_areas = jnp.where(
        jnp.expand_dims(gojax.get_turns(end_states), (1, 2, 3)),
        final_areas[:, [1, 0]], final_areas)
    return GameData(start_states=start_states,
                    end_states=end_states,
                    nk_actions=nk_actions,
                    start_player_final_areas=start_player_final_areas,
                    end_player_final_areas=end_player_final_areas)


def sample_trajectories(trajectories: game.Trajectories, sample_size: int,
                        rng_key: jax.random.KeyArray) -> game.Trajectories:
    """Samples non-terminal states and actions from trajectories."""
    orig_batch_size, orig_traj_len = trajectories.nt_states.shape[:2]
    game_ended = nt_utils.unflatten_first_dim(
        gojax.get_ended(nt_utils.flatten_first_two_dims(
            trajectories.nt_states)), orig_batch_size, orig_traj_len)
    sample_state_logits = game_ended * float('-inf')
    gumbel = jax.random.gumbel(rng_key, sample_state_logits.shape)
    _, subset_indices = jax.lax.top_k(sample_state_logits + gumbel,
                                      k=sample_size)
    sorted_subset_indices = jax.lax.sort(subset_indices, dimension=-1)
    batch_indices = jnp.arange(orig_batch_size).reshape(-1, 1)
    return trajectories.replace(
        nt_states=trajectories.nt_states[batch_indices, sorted_subset_indices],
        nt_actions=trajectories.nt_actions[batch_indices,
                                           sorted_subset_indices])
