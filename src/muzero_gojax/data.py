"""Processes and samples data from games for model updates."""

import chex
import gojax
import jax
import jax.numpy as jnp

from muzero_gojax import game, nt_utils


@chex.dataclass(frozen=True)
class GameData:
    """Game data.

    The model is trained to predict the following:
    • The end state given the start state and the actions taken
    • The end reward given the start state and the actions taken
    • The start reward given the start state
    """
    start_states: jnp.ndarray
    # Actions taken from start state to end state. A value of -1 indicates that
    # the previous value was the last action taken. k is currently hardcoded to
    # 4 because we assume that's the max number of hypothetical steps we'll
    # use.
    nk_actions: jnp.ndarray
    end_states: jnp.ndarray
    start_labels: jnp.ndarray  # {-1, 0, 1}
    end_labels: jnp.ndarray  # {-1, 0, 1}


def sample_game_data(trajectories: game.Trajectories,
                     rng_key: jax.random.KeyArray,
                     max_hypothetical_steps: int) -> GameData:
    """Samples game data from trajectories.

    For each trajectory, we independently sample our hypothetical step value k
    uniformly from [1, max_hypothetical_steps]. We then sample two states from
    the trajectory. The index of the first state, i, is sampled uniformly from
    all non-terminal states of the trajectory. The index of the second state, j,
    is min(i+k, n) where n is the length of the trajectory 
    (including the terminal state).

    Args:
        trajectories: Trajectories from a game.
        rng_key: Random key for sampling.
        max_hypothetical_steps: Maximum number of hypothetical steps to use.

    Returns:
        Game data sampled from trajectories.
    """
    batch_size, traj_len = trajectories.nt_states.shape[:2]
    k = jax.random.randint(rng_key,
                           shape=(batch_size, ),
                           minval=1,
                           maxval=max_hypothetical_steps + 1)
    next_k_indices = jnp.repeat(jnp.expand_dims(jnp.arange(traj_len), axis=0),
                                batch_size,
                                axis=0)
    batch_order_indices = jnp.arange(batch_size)
    game_ended = nt_utils.unflatten_first_dim(
        gojax.get_ended(nt_utils.flatten_first_two_dims(
            trajectories.nt_states)), batch_size, traj_len)
    base_sample_state_logits = game_ended * float('-inf')
    game_len = jnp.sum(~game_ended, axis=1)
    start_indices = jax.random.categorical(rng_key,
                                           base_sample_state_logits,
                                           axis=1)
    chex.assert_rank(start_indices, 1)
    chex.assert_equal_shape([start_indices, game_len, k])
    end_indices = jnp.minimum(start_indices + k, game_len)
    start_states = trajectories.nt_states[batch_order_indices, start_indices]
    end_states = trajectories.nt_states[batch_order_indices, end_indices]
    nk_actions = trajectories.nt_actions[
        jnp.expand_dims(batch_order_indices, axis=1),
        jnp.expand_dims(start_indices, axis=1) + next_k_indices]
    nk_actions = nk_actions.at[batch_order_indices, end_indices].set(-1)
    nt_player_labels = game.get_nt_player_labels(trajectories.nt_states)
    start_labels = nt_player_labels[batch_order_indices, start_indices]
    end_labels = nt_player_labels[batch_order_indices, end_indices]
    return GameData(start_states=start_states,
                    end_states=end_states,
                    nk_actions=nk_actions,
                    start_labels=start_labels,
                    end_labels=end_labels)