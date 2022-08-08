import haiku as hk
import jax.nn
import jax.tree_util
import optax
from jax import lax
from jax import numpy as jnp

from muzero_gojax import game


def nt_categorical_cross_entropy(x_logits: jnp.ndarray, y_logits: jnp.ndarray, temp: float = None,
                                 nt_mask: jnp.ndarray = None):
    """
    Categorical cross-entropy with respect to the last dimension.

    :param x_logits: N x T float array
    :param y_logits: N x T float array
    :param temp: temperature constant
    :param nt_mask: 0-1 mask to determine which logits to consider.
    :return: Mean cross-entropy loss between the softmax of x and softmax of (y / temp)
    """
    if temp is None:
        temp = 1
    if nt_mask is None:
        nt_mask = jnp.ones(x_logits.shape[:-1])
    cross_entropy = -jnp.sum(jax.nn.softmax(y_logits / temp) * jax.nn.log_softmax(x_logits), axis=-1)

    return jnp.sum(cross_entropy * nt_mask) / jnp.sum(nt_mask, dtype='bfloat16')


def nt_sigmoid_cross_entropy(value_logits: jnp.ndarray, labels: jnp.ndarray, nt_mask: jnp.ndarray = None):
    """
    Computes the sigmoid cross-entropy given binary labels and logit values.

    :param value_logits: N x T float array
    :param labels: N x T integer array of binary (0, 1) values
    :param nt_mask: 0-1 mask to determine which logits to consider.
    :return: Mean cross-entropy loss between the sigmoid of the value logits and the labels.
    """
    if nt_mask is None:
        nt_mask = jnp.ones_like(value_logits)
    cross_entropy = -labels * jax.nn.log_sigmoid(value_logits) - (1 - labels) * jax.nn.log_sigmoid(-value_logits)
    return jnp.sum(cross_entropy * nt_mask) / jnp.sum(nt_mask, dtype='bfloat16')


def make_nt_mask(batch_size: int, total_steps: int, step: int) -> jnp.ndarray:
    """
    Creates a boolean mask of shape batch_size x total_steps, where the `step` columns (0-index, exclusive) are True
    and the rest are false.

    For example, make_nt_mask(2, 2, 1) = [[True, False], [True, False]].
    """
    return jnp.repeat(jnp.expand_dims(jnp.arange(total_steps) < step, 0), batch_size, axis=0)


def compute_policy_loss(policy_model, value_model, params: optax.Params, model_state: dict, i: int,
                        transitions: jnp.ndarray, nt_embeds: jnp.ndarray, temp: float):
    """
    Computes the softmax cross entropy loss using -value_model(transitions) as the labels and the
    policy_model(nt_embeddings) as the training logits.

    To prevent training the value model, the gradient flow is cut off from the value model.

    :param policy_model: Policy model.
    :param value_model: Value model.
    :param params: Parameters.
    :param model_state: Model state.
    :param i: Iteration index when this function is used in fori_loops.
    :param transitions: N x T x A x (D^m) array where D^m represents the Go embedding shape.
    :param nt_embeds: N x T x (D^m) array where D^m represents the Go embedding shape.
    :param temp: Temperature adjustment for value model labels.
    :return: Scalar float value and updated model state.
    """
    # pylint: disable=too-many-arguments
    batch_size, total_steps, action_size = transitions.shape[:3]
    embed_shape = transitions.shape[3:]
    num_examples = batch_size * total_steps
    # transition_value_logits is a 1-D vector of length N * T * A.
    flat_transition_value_logits, model_state = value_model(params, model_state, None, jnp.reshape(transitions, (
        num_examples * action_size,) + embed_shape))
    trajectory_policy_shape = (batch_size, total_steps, action_size)
    transition_value_logits = jnp.reshape(flat_transition_value_logits, trajectory_policy_shape)
    policy_logits, model_state = policy_model(params, model_state, None,
                                              jnp.reshape(nt_embeds, (num_examples,) + embed_shape))
    # Note we take the negative of the transition value logits.
    return nt_categorical_cross_entropy(jnp.reshape(policy_logits, trajectory_policy_shape),
                                        -lax.stop_gradient(transition_value_logits), temp,
                                        nt_mask=make_nt_mask(batch_size, total_steps, total_steps - i)), model_state


def compute_value_loss(value_model, params: optax.Params, model_state: dict, i: int, nt_embeds: jnp.ndarray,
                       nt_game_winners: jnp.ndarray):
    """
    Computes the binary cross entropy loss between sigmoid(value_model(nt_embeds)) and
    nt_game_winners.

    :param value_model: Value model.
    :param params: Parameters of value model.
    :param model_state: Model state.
    :param i: i'th hypothetical step.
    :param nt_embeds: An N x T x (D*) array of Go state embeddings.
    :param nt_game_winners: An N x T integer array of length N. 1 = black won, 0 = tie,
    -1 = white won.
    :return: Scalar float value, and updated model state.
    """
    batch_size, total_steps = nt_embeds.shape[:2]
    embed_shape = nt_embeds.shape[2:]
    num_examples = batch_size * total_steps
    labels = (jnp.roll(nt_game_winners, shift=i) + 1) / 2
    flat_value_logits, model_state = value_model(params, model_state, None,
                                                 jnp.reshape(nt_embeds, (num_examples,) + embed_shape))
    return nt_sigmoid_cross_entropy(jnp.reshape(flat_value_logits, (batch_size, total_steps)), labels,
                                    nt_mask=make_nt_mask(batch_size, total_steps, total_steps - i)), model_state


def compute_embed_loss(transition_embeds: jnp.ndarray, target_embeds: jnp.ndarray, nt_mask: jnp.ndarray):
    """
    Computes the mean-square error between the embedding output of the embed model and transition model.

    Cuts off the gradient-flow from the embed model. We want the transition model to act like the embedding model.

    :param transition_embeds: N x T x (D*) float array.
    :param target_embeds: N x T x (D*) float array.
    :param nt_mask: N x T boolean array.
    :return: scalar float.
    """
    reduce_axes = tuple(range(2, len(transition_embeds.shape)))
    return jnp.sum(
        jnp.sum((transition_embeds.astype('bfloat16') - lax.stop_gradient(target_embeds).astype('bfloat16')) ** 2,
                axis=reduce_axes) * nt_mask) / jnp.sum(nt_mask, dtype='bfloat16')


def update_k_step_losses(go_model: hk.MultiTransformedWithState, params: optax.Params, temp: float, i: int, data: dict):
    """
    Updates data to the i'th hypothetical step and adds the corresponding value and policy losses
    at that step.

    :param go_model: Haiku model architecture.
    :param params: Parameters of the model.
    :param temp: Temperature for policy cross entropy labels.
    :param i: The index of the hypothetical step (0-indexed).
    :param data: A dictionary structure of the format
        'nt_embeds': An N x T x (D*) array of Go state embeddings.
        'nt_actions': An N x T non-negative integer array.
        'nt_game_winners': An N x T integer array of length N. 1 = black won, 0 = tie, -1 = white
        won.
        'cum_val_loss': Cumulative value loss.
    :return: An updated version of data.
    """
    embed_model, value_model, policy_model, transition_model = go_model.apply
    batch_size, total_steps = data['nt_embeds'].shape[:2]
    num_examples = batch_size * total_steps
    embed_shape = data['nt_embeds'].shape[2:]

    # Update the cumulative value loss.
    value_loss, data['model_state'] = compute_value_loss(value_model, params, data['model_state'], i, data['nt_embeds'],
                                                         data['nt_game_winners'])
    data['cum_val_loss'] += value_loss

    # Get the transitions.
    # Flattened transitions is (N * T) x A x (D*)
    flat_transitions, data['model_state'] = transition_model(params, data['model_state'], None,
                                                             jnp.reshape(data['nt_embeds'],
                                                                         (num_examples,) + embed_shape))
    transitions = jnp.reshape(flat_transitions, (batch_size, total_steps, flat_transitions.shape[1]) + embed_shape)

    # Update the cumulative policy loss.
    policy_loss, data['model_state'] = compute_policy_loss(policy_model, value_model, params, data['model_state'], i,
                                                           transitions, data['nt_embeds'], temp)
    data['cum_policy_loss'] += policy_loss

    # Update the state embeddings from the transitions indexed by the played actions.
    nt_next_embeds = jnp.reshape(
        flat_transitions[jnp.arange(num_examples), jnp.reshape(data['nt_actions'], num_examples)],
        (batch_size, total_steps, *embed_shape))
    nt_hypothetical_embeds = jnp.roll(nt_next_embeds, -1, axis=1)

    # Compute the transition's embedding loss.
    def _compute_embed_loss():
        return compute_embed_loss(nt_hypothetical_embeds, data['nt_embeds'],
                                  make_nt_mask(batch_size, total_steps, total_steps - i - 1))

    def _do_nothing():
        return jnp.zeros((), dtype='bfloat16')

    data['cum_embed_loss'] += lax.cond(total_steps - i - 1 > 0, _compute_embed_loss, _do_nothing)

    # Update the embeddings.
    data['nt_embeds'] = nt_hypothetical_embeds

    return data


def compute_k_step_losses(go_model: hk.MultiTransformedWithState, params: optax.Params, model_state: dict,
                          trajectories: jnp.ndarray, k=1, temp: float = 1):
    """
    Computes the value, and policy k-step losses.

    :param go_model: Haiku model architecture.
    :param params: Parameters of the model.
    :param model_state: Model state.
    :param trajectories: An N x T X C X H x W boolean array.
    :param k: Number of hypothetical steps.
    :param temp: Temperature for policy cross entropy label logits.
    :return: A dictionary of cumulative losses and model state
    """
    embed_model = go_model.apply[0]
    batch_size, total_steps, channels, nrows, ncols = trajectories.shape
    embeddings, model_state = embed_model(params, model_state, None,
                                          jnp.reshape(trajectories, (batch_size * total_steps, channels, nrows, ncols)))
    embed_shape = embeddings.shape[1:]
    actions, game_winners = game.get_actions_and_labels(trajectories)
    data = lax.fori_loop(lower=0, upper=k, body_fun=jax.tree_util.Partial(update_k_step_losses, go_model, params, temp),
                         init_val={
                             'model_state': model_state,
                             'nt_embeds': jnp.reshape(embeddings, (batch_size, total_steps) + embed_shape),
                             'nt_actions': actions, 'nt_game_winners': game_winners, 'cum_embed_loss': 0,
                             'cum_val_loss': 0, 'cum_policy_loss': 0,
                         })
    return {key: data[key] for key in ['cum_embed_loss', 'cum_val_loss', 'cum_policy_loss', 'model_state']}


def compute_k_step_total_loss(go_model: hk.MultiTransformedWithState, params: optax.Params, model_state: dict,
                              trajectories: jnp.ndarray, k: int = 1, temp: float = 1):
    """
    Computes the sum of all losses.

    Use this function to compute the gradient of the model parameters.

    :param go_model: Haiku model architecture.
    :param params: Parameters of the model.
    :param model_state: Model state.
    :param trajectories: An N x T X C X H x W boolean array.
    :param k: Number of hypothetical steps.
    :param temp: Temperature for policy cross entropy label logits.
    :return: The total loss, and a dictionary of each cumulative loss + the updated model state
    """
    metrics_data = compute_k_step_losses(go_model, params, model_state, trajectories, k, temp)
    return metrics_data['cum_embed_loss'] + metrics_data['cum_val_loss'] + metrics_data['cum_policy_loss'], metrics_data
