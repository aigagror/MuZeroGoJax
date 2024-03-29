"""Module for understanding the behavior of the code."""
import functools
import itertools
import os
from typing import Optional

import chex
import haiku as hk
import jax
import jax.random
import optax
import pandas as pd
from absl import flags
from jax import numpy as jnp
from matplotlib import patches
from matplotlib import pyplot as plt
from matplotlib.ticker import MaxNLocator

import gojax
from muzero_gojax import data, drive, game, logger, models, nt_utils

_PLOT_TRAJECTORY_SAMPLE_SIZE = flags.DEFINE_integer(
    'plot_trajectory_sample_size', 8,
    'Number of states and actions to sample from trajectories. '
    '0 or less means plots all.')


@chex.dataclass(frozen=True)
class ModelThoughts:
    """Model thoughts."""
    nt_values: jnp.ndarray
    nt_policies: jnp.ndarray
    nt_final_areas: jnp.ndarray
    nt_qvalues: jnp.ndarray


def _plot_state(axis, state: jnp.ndarray):
    axis.imshow(state[gojax.BLACK_CHANNEL_INDEX].astype(int) -
                state[gojax.WHITE_CHANNEL_INDEX].astype(int),
                vmin=-1,
                vmax=1,
                cmap='Greys')
    board_size = state.shape[-1]
    edgecolor = 'yellow' if jnp.alltrue(
        state[gojax.TURN_CHANNEL_INDEX]) else 'blue'
    turn_rect = patches.Rectangle(xy=(-0.5, -0.5),
                                  width=board_size,
                                  height=board_size,
                                  linewidth=12,
                                  edgecolor=edgecolor,
                                  facecolor='none')
    axis.add_patch(turn_rect)
    if jnp.alltrue(state[gojax.END_CHANNEL_INDEX]):
        end_rect = patches.Rectangle(xy=(-0.5, -0.5),
                                     width=board_size,
                                     height=board_size,
                                     linewidth=6,
                                     edgecolor='maroon',
                                     facecolor='none')
        axis.add_patch(end_rect)
    elif jnp.alltrue(state[gojax.PASS_CHANNEL_INDEX]):
        pass_rect = patches.Rectangle(xy=(-0.5, -0.5),
                                      width=board_size,
                                      height=board_size,
                                      linewidth=6,
                                      edgecolor='orange',
                                      facecolor='none')
        axis.add_patch(pass_rect)


def plot_train_metrics_by_regex(train_metrics_df: pd.DataFrame, regexes=None):
    """Plots the metrics dataframe grouped by regex's."""
    if regexes is None:
        regexes = [
            '.+_entropy',
            '.+_acc',
            '.+_loss',
            '(.+_wins|ties|avg_game_length)',
            '.+winrate',
        ]
    _, axes = plt.subplots(len(regexes),
                           2,
                           figsize=(12, 3 * len(regexes)),
                           squeeze=False)
    for i, regex in enumerate(regexes):
        sub_df = train_metrics_df.filter(regex=regex).dropna().astype(float)
        if sub_df.empty:
            continue
        sub_df.plot(ax=axes[i, 0])
        sub_df.plot(logy=True, ax=axes[i, 1])
    plt.tight_layout()


def plot_trajectories(sparse_trajectories: data.SparseTrajectories,
                      model_thoughts: Optional[ModelThoughts] = None,
                      title: Optional[str] = None):
    """Plots trajectories."""
    batch_size, traj_len, _, board_size, _ = sparse_trajectories.nt_states.shape
    if model_thoughts is not None:
        # State, action probabilities, pass & value,
        # empty row for buffer
        nrows = batch_size * 5
    else:
        nrows = batch_size

    player_labels = game.get_nt_player_labels(sparse_trajectories.nt_states)
    fig, axes = plt.subplots(nrows,
                             traj_len,
                             figsize=(traj_len * 2.5, nrows * 2.5))
    if title is not None:
        plt.suptitle(title)
    for batch_idx, traj_idx in itertools.product(range(batch_size),
                                                 range(traj_len)):
        if model_thoughts is not None:
            group_start_row_idx = batch_idx * 5
        else:
            group_start_row_idx = batch_idx
        # Plot state
        _plot_state(axes[group_start_row_idx, traj_idx],
                    sparse_trajectories.nt_states[batch_idx, traj_idx])
        # Annotate action
        action_1d = sparse_trajectories.nt_actions[batch_idx, traj_idx -
                                                   1] if traj_idx > 0 else None
        if action_1d is not None:
            if action_1d < board_size**2:
                rect = patches.Rectangle(
                    xy=(float(action_1d % board_size - 0.5),
                        float(action_1d // board_size - 0.5)),
                    width=1,
                    height=1,
                    linewidth=2,
                    edgecolor='g',
                    facecolor='none')
                axes[group_start_row_idx, traj_idx].add_patch(rect)
        # I forgot what this does...
        axes[group_start_row_idx,
             traj_idx].xaxis.set_major_locator(MaxNLocator(integer=True))
        axes[group_start_row_idx,
             traj_idx].yaxis.set_major_locator(MaxNLocator(integer=True))
        # Label winner in title.
        axes[group_start_row_idx, traj_idx].set_title({
            1: 'won',
            0: 'Tie',
            -1: 'Lost'
        }[int(player_labels[batch_idx, traj_idx])])

        if model_thoughts is not None:
            # Plot action probabilities.
            policies = jnp.reshape(
                model_thoughts.nt_policies[batch_idx, traj_idx, :-1],
                (board_size, board_size))
            axes[group_start_row_idx + 1, traj_idx].set_title('Actions')
            image = axes[group_start_row_idx + 1, traj_idx].imshow(policies,
                                                                   vmin=0,
                                                                   vmax=1)
            fig.colorbar(image, ax=axes[group_start_row_idx + 1, traj_idx])
            # Plot final areas.
            axes[group_start_row_idx + 2, traj_idx].set_title('Final areas')
            final_areas = model_thoughts.nt_final_areas[batch_idx, traj_idx]
            image = axes[group_start_row_idx + 2,
                         traj_idx].imshow(jnp.moveaxis(
                             jnp.concatenate([
                                 final_areas,
                                 jnp.zeros((1, board_size, board_size))
                             ]), 0, -1),
                                          vmin=0,
                                          vmax=1)
            fig.colorbar(image, ax=axes[group_start_row_idx + 2, traj_idx])
            # Plot hypothetical q-values.
            hypo_qvalue = jnp.reshape(
                model_thoughts.nt_qvalues[batch_idx, traj_idx, :-1],
                (board_size, board_size))
            axes[group_start_row_idx + 3, traj_idx].set_title('Q-values')
            image = axes[group_start_row_idx + 3, traj_idx].imshow(hypo_qvalue)
            fig.colorbar(image, ax=axes[group_start_row_idx + 3, traj_idx])
            # Plot pass, value, and their hypothetical variants..
            axes[group_start_row_idx + 4, traj_idx].set_title('Pass & Values')
            plt.ylim(0, 1)
            axes[group_start_row_idx + 4,
                 traj_idx].bar(['pass', 'q-pass', 'value'], [
                     model_thoughts.nt_policies[batch_idx, traj_idx, -1],
                     model_thoughts.nt_qvalues[batch_idx, traj_idx, -1],
                     model_thoughts.nt_values[batch_idx, traj_idx],
                 ])

    plt.tight_layout()


def get_model_thoughts(go_model: hk.MultiTransformed, params: optax.Params,
                       trajectories: game.Trajectories,
                       rng_key: jax.random.KeyArray):
    """Returns model thoughts for a batch of trajectories."""
    states = nt_utils.flatten_first_two_dims(trajectories.nt_states)
    embeddings = go_model.apply[models.EMBED_INDEX](params, rng_key, states)
    final_areas = jax.nn.sigmoid(go_model.apply[models.VALUE_INDEX](
        params, rng_key, embeddings).astype('float32'))
    values = jnp.sum(final_areas[:, 0], axis=(1, 2)) - jnp.sum(
        final_areas[:, 1], axis=(1, 2))
    policies = jax.nn.softmax(go_model.apply[models.POLICY_INDEX](
        params, rng_key, embeddings).astype('float32'))
    batch_size, traj_length = trajectories.nt_states.shape[:2]
    all_transitions = go_model.apply[models.TRANSITION_INDEX](params, rng_key,
                                                              embeddings)
    qvalues = -models.get_tromp_taylor_score(
        go_model.apply[models.VALUE_INDEX](
            params, rng_key, nt_utils.flatten_first_two_dims(all_transitions)))
    return jax.tree_map(
        lambda x: x.astype(jnp.float32),
        ModelThoughts(
            nt_values=nt_utils.unflatten_first_dim(values, batch_size,
                                                   traj_length),
            nt_policies=nt_utils.unflatten_first_dim(policies, batch_size,
                                                     traj_length),
            nt_final_areas=nt_utils.unflatten_first_dim(
                final_areas, batch_size, traj_length),
            nt_qvalues=nt_utils.unflatten_first_dim(qvalues, batch_size,
                                                    traj_length,
                                                    states.shape[-1]**2 + 1)))


def print_param_size_analysis(params: optax.Params):
    """Prints the number of parameters in each sub-model."""
    logger.log(f'{hk.data_structures.tree_size(params)} parameters.')

    def _regex_in_dict_item(regex: str, item: tuple):
        return regex in item[0]

    for sub_model_regex in ['embed', 'area', 'value', 'policy', 'transition']:
        sub_model_params = dict(
            filter(functools.partial(_regex_in_dict_item, sub_model_regex),
                   params.items()))
        logger.log(
            f'\t{sub_model_regex}: {hk.data_structures.tree_size(sub_model_params)}'
        )


def eval_elo(go_model: hk.MultiTransformed, params: optax.Params,
             board_size: int) -> dict:
    """Evaluates the ELO by pitting it against baseline models."""
    logger.log('Evaluating elo with 256 games per opponent benchmark...')
    n_games = 256
    base_policy_model = models.get_policy_model(go_model,
                                                params,
                                                sample_action_size=0)
    ppit = jax.pmap(game.pit, static_broadcasted_argnums=(0, 1, 2, 3, 4))
    rng_keys = jax.random.split(jax.random.PRNGKey(42),
                                jax.local_device_count())
    eval_dict = {}
    traj_len = 2 * board_size**2
    for benchmark in models.get_benchmarks(board_size):
        wins, ties, losses = ppit(base_policy_model, benchmark.policy,
                                  board_size,
                                  n_games // jax.local_device_count(),
                                  traj_len, rng_keys)
        wins = jnp.sum(wins)
        ties = jnp.sum(ties)
        losses = jnp.sum(losses)
        win_rate = (wins + ties / 2) / n_games
        logger.log(f"Base v. {benchmark.name}: {win_rate:.3f} win rate "
                   f"| {wins} wins, {ties} ties, {losses} losses")
        eval_dict[f'{benchmark.name}-winrate'] = win_rate
    return eval_dict


def plot_all_metrics(go_model: hk.MultiTransformed, params: optax.Params,
                     metrics_df: pd.DataFrame, board_size: int):
    """Plots all metrics."""
    if len(metrics_df) > 0:
        logger.log("Plotting training metrics.")
        plot_train_metrics_by_regex(metrics_df)
    else:
        logger.log("No training metrics to plot.")
    policy_model = models.get_policy_model(go_model, params)
    random_policy = models.get_policy_model(models.make_random_model(),
                                            params={})
    rng_key = jax.random.PRNGKey(42)
    sample_traj = game.self_play(
        game.new_trajectories(board_size,
                              batch_size=3,
                              trajectory_length=2 * board_size**2),
        policy_model, rng_key)
    sampled_sample_traj = data.sample_sparse_trajectories(
        sample_traj, _PLOT_TRAJECTORY_SAMPLE_SIZE.value, rng_key)
    logger.log('Plotting sample trajectories.')
    plot_trajectories(sampled_sample_traj,
                      get_model_thoughts(go_model, params, sampled_sample_traj,
                                         rng_key),
                      title='Sample Trajectories')

    random_traj: game.Trajectories = game.self_play(
        game.new_trajectories(board_size,
                              batch_size=3,
                              trajectory_length=2 * board_size**2),
        random_policy, rng_key)
    sampled_random_traj = data.sample_sparse_trajectories(
        random_traj, _PLOT_TRAJECTORY_SAMPLE_SIZE.value, rng_key)
    logger.log('Plotting random trajectories.')
    plot_trajectories(sampled_random_traj,
                      get_model_thoughts(go_model, params, sampled_random_traj,
                                         rng_key),
                      title='Random Trajectories')
    plt.show(block=False)


def metrics_logs_to_df(metrics_logs: list):
    """Converts metrics logs to a dataframe."""
    if len(metrics_logs) == 0:
        return pd.DataFrame()
    return pd.json_normalize(metrics_logs).set_index('step')


def save_metrics_logs(directory: str, metrics_logs: list):
    """Saves metrics logs to a directory."""
    metrics_df = metrics_logs_to_df(metrics_logs)
    drive.write_file(os.path.join(directory, 'metrics.csv'),
                     mode='wt',
                     mime_type='application/csv',
                     write_fn=metrics_df.to_csv)


def load_metrics_logs(directory: str):
    """Loads metrics logs from a directory."""
    with drive.open_file(os.path.join(directory, 'metrics.csv'),
                         'rt',
                         encoding='utf-8') as metrics_fp:
        metrics_logs = pd.read_csv(metrics_fp)
    return metrics_logs.to_dict(orient='records')
