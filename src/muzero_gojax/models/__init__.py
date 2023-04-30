"""High-level model management."""
# pylint:disable=duplicate-code

import dataclasses
import json
import os
import pickle
from types import ModuleType
from typing import Callable, List, Tuple

import chex
import haiku as hk
import jax.numpy as jnp
import jax.random
import jax.tree_util
import optax
from absl import flags

import gojax
from muzero_gojax import logger, nt_utils
from muzero_gojax.models import (_area, _base, _build_config, _embed, _policy,
                                 _transition, _value)
from muzero_gojax.models._area import *
# pylint: disable=unused-import
from muzero_gojax.models._build_config import *
from muzero_gojax.models._embed import *
from muzero_gojax.models._policy import *
from muzero_gojax.models._transition import *
from muzero_gojax.models._value import *

_TRAINED_MODELS_DIR = flags.DEFINE_string(
    'trained_models_dir', './trained_models/',
    'Directory containing trained weights.')

_QVAL_SCALE = flags.DEFINE_float(
    'qval_scale', 1.0, 'Q-value scale. '
    'Sigma from the MuZero Go paper.')

EMBED_INDEX = 0
AREA_INDEX = 1
VALUE_INDEX = 2
POLICY_INDEX = 3
TRANSITION_INDEX = 4


@chex.dataclass(frozen=True)
class PolicyOutput:
    """Policy output."""
    # N
    sampled_actions: jnp.ndarray
    # TODO: Fill out and use the fields below.
    # N x A'
    visited_actions: jnp.ndarray
    # N x A'
    visited_qvalues: jnp.ndarray


@chex.dataclass(frozen=True)
class ValueOutput:
    """Value output."""
    # N
    value: jnp.ndarray


# RNG, Go State -> Action.
PolicyModel = Callable[[jax.random.KeyArray, jnp.ndarray], PolicyOutput]

# RNG, Go State -> Value.
ValueModel = Callable[[jax.random.KeyArray, jnp.ndarray], ValueOutput]


@chex.dataclass(frozen=True)
class Benchmark:
    """Benchmark model."""
    policy: PolicyModel
    name: str


def _fetch_submodel(
        submodel_module: ModuleType,
        submodel_build_config: _build_config.SubModelBuildConfig,
        model_build_config: _build_config.ModelBuildConfig
) -> _base.BaseGoModel:
    model_registry = dict([(name, cls)
                           for name, cls in submodel_module.__dict__.items()
                           if isinstance(cls, type)])
    return model_registry[submodel_build_config.name_key](
        model_build_config, submodel_build_config)


def _build_model_transform(
    all_models_build_config: _build_config.AllModelsBuildConfig
) -> hk.MultiTransformed:
    """Builds a multi-transformed Go model."""

    def f():
        # pylint: disable=invalid-name
        embed_model = _fetch_submodel(
            _embed, all_models_build_config.embed_build_config,
            all_models_build_config.model_build_config)
        area_model = _fetch_submodel(
            _area, all_models_build_config.area_build_config,
            all_models_build_config.model_build_config)
        value_model = _fetch_submodel(
            _value, all_models_build_config.value_build_config,
            all_models_build_config.model_build_config)
        policy_model = _fetch_submodel(
            _policy, all_models_build_config.policy_build_config,
            all_models_build_config.model_build_config)
        transition_model = _fetch_submodel(
            _transition, all_models_build_config.transition_build_config,
            all_models_build_config.model_build_config)

        def init(states):
            embedding = embed_model(states)
            decoding = area_model(embedding)
            policy_logits = policy_model(embedding)
            transition_logits = transition_model(embedding)
            value_logits = value_model(embedding)
            return decoding, value_logits, policy_logits, transition_logits

        return init, (embed_model, area_model, value_model, policy_model,
                      transition_model)

    return hk.multi_transform(f)


def build_model_with_params(
        all_models_build_config: _build_config.AllModelsBuildConfig,
        rng_key: jax.random.KeyArray
) -> Tuple[hk.MultiTransformed, optax.Params]:
    """
    Builds the corresponding model for the given name.

    :param board_size: Board size
    :return: A Haiku multi-transformed Go model consisting of (1) a state embedding model,
    (2) a policy model, (3) a transition model, and (4) a value model.
    """

    go_model = _build_model_transform(all_models_build_config)
    params = go_model.init(
        rng_key,
        gojax.new_states(all_models_build_config.model_build_config.board_size,
                         1))
    logger.log("Initialized parameters randomly.")
    return go_model, params


def load_model(
    load_dir: str
) -> Tuple[hk.MultiTransformed, optax.Params,
           _build_config.AllModelsBuildConfig]:
    """Loads the model from the given directory.

    Expects there to be one config.json file for the AllModelsBuildConfig
    and a params.npz file for the parameters.

    Args:
        load_dir (str): Model directory.

    Returns:
        Go model, parameters, and build config.
    """
    with open(os.path.join(load_dir, 'build_config.json'),
              'rt',
              encoding='utf-8') as config_fp:
        json_dict = json.load(config_fp)
        model_build_config = _build_config.ModelBuildConfig(
            **json_dict['model_build_config'])
        all_models_build_config = _build_config.AllModelsBuildConfig(
            model_build_config=model_build_config,
            embed_build_config=_build_config.SubModelBuildConfig(
                **json_dict['embed_build_config']),
            area_build_config=_build_config.SubModelBuildConfig(
                **json_dict['area_build_config']),
            value_build_config=_build_config.SubModelBuildConfig(
                **json_dict['value_build_config']),
            policy_build_config=_build_config.SubModelBuildConfig(
                **json_dict['policy_build_config']),
            transition_build_config=_build_config.SubModelBuildConfig(
                **json_dict['transition_build_config']),
        )

    with open(os.path.join(load_dir, 'params.npz'), 'rb') as file_array:
        params = pickle.load(file_array)
    go_model = _build_model_transform(all_models_build_config)
    return go_model, params, all_models_build_config


def make_random_model():
    """Makes a random normal model."""
    all_models_build_config = _build_config.AllModelsBuildConfig(
        model_build_config=_build_config.ModelBuildConfig(
            embed_dim=gojax.NUM_CHANNELS),
        embed_build_config=_build_config.SubModelBuildConfig(
            name_key='IdentityEmbed'),
        area_build_config=_build_config.SubModelBuildConfig(
            name_key='AmplifiedDecode'),
        value_build_config=_build_config.SubModelBuildConfig(
            name_key='RandomValue'),
        policy_build_config=_build_config.SubModelBuildConfig(
            name_key='RandomPolicy'),
        transition_build_config=_build_config.SubModelBuildConfig(
            name_key='RandomTransition'),
    )
    return _build_model_transform(all_models_build_config)


def make_random_policy_tromp_taylor_value_model():
    """Random normal policy with tromp taylor value."""
    all_models_build_config = _build_config.AllModelsBuildConfig(
        model_build_config=_build_config.ModelBuildConfig(
            embed_dim=gojax.NUM_CHANNELS),
        embed_build_config=_build_config.SubModelBuildConfig(
            name_key='IdentityEmbed'),
        area_build_config=_build_config.SubModelBuildConfig(
            name_key='AmplifiedDecode'),
        value_build_config=_build_config.SubModelBuildConfig(
            name_key='TrompTaylorValue'),
        policy_build_config=_build_config.SubModelBuildConfig(
            name_key='RandomPolicy'),
        transition_build_config=_build_config.SubModelBuildConfig(
            name_key='RealTransition'),
    )
    return _build_model_transform(all_models_build_config)


def make_tromp_taylor_model():
    """Makes a Tromp Taylor (greedy) model."""
    all_models_build_config = _build_config.AllModelsBuildConfig(
        model_build_config=_build_config.ModelBuildConfig(
            embed_dim=gojax.NUM_CHANNELS),
        embed_build_config=_build_config.SubModelBuildConfig(
            name_key='IdentityEmbed'),
        area_build_config=_build_config.SubModelBuildConfig(
            name_key='AmplifiedDecode'),
        value_build_config=_build_config.SubModelBuildConfig(
            name_key='TrompTaylorValue'),
        policy_build_config=_build_config.SubModelBuildConfig(
            name_key='TrompTaylorPolicy'),
        transition_build_config=_build_config.SubModelBuildConfig(
            name_key='RealTransition'))
    return _build_model_transform(all_models_build_config)


def make_tromp_taylor_amplified_model():
    """Makes a Tromp Taylor amplified (greedy) model."""
    all_models_build_config = _build_config.AllModelsBuildConfig(
        model_build_config=_build_config.ModelBuildConfig(
            embed_dim=gojax.NUM_CHANNELS),
        embed_build_config=_build_config.SubModelBuildConfig(
            name_key='IdentityEmbed'),
        area_build_config=_build_config.SubModelBuildConfig(
            name_key='AmplifiedDecode'),
        value_build_config=_build_config.SubModelBuildConfig(
            name_key='TrompTaylorValue'),
        policy_build_config=_build_config.SubModelBuildConfig(
            name_key='TrompTaylorAmplifiedPolicy'),
        transition_build_config=_build_config.SubModelBuildConfig(
            name_key='RealTransition'))
    return _build_model_transform(all_models_build_config)


def get_benchmarks(board_size: int) -> List[Benchmark]:
    """Returns the set of all benchmarks compatible with the board size.

    Includes trained models.
    """
    benchmarks: List[Benchmark] = [
        Benchmark(policy=get_policy_model(
            make_random_policy_tromp_taylor_value_model(), params={}),
                  name='Random'),
        Benchmark(policy=get_policy_model(make_tromp_taylor_model(),
                                          params={}),
                  name='Tromp Taylor'),
        Benchmark(policy=get_policy_model(make_tromp_taylor_amplified_model(),
                                          params={}),
                  name='Tromp Taylor Amplified')
    ]

    if os.path.exists(_TRAINED_MODELS_DIR.value):
        for item in os.listdir(_TRAINED_MODELS_DIR.value):
            model_dir = os.path.join(
                _TRAINED_MODELS_DIR.value,
                item,
            )
            if os.path.isdir(model_dir):
                with open(os.path.join(model_dir, 'build_config.json'),
                          'rt',
                          encoding='utf-8') as config_fp:
                    json_dict = json.load(config_fp)
                    model_build_config = _build_config.ModelBuildConfig(
                        **json_dict['model_build_config'])
                    if model_build_config.board_size != board_size:
                        continue
                try:
                    go_model, trained_params, all_models_config = load_model(
                        model_dir)
                    if all_models_config.model_build_config.board_size != board_size:
                        continue
                    base_trained_policy = get_policy_model(
                        go_model, trained_params)
                    benchmarks.append(
                        Benchmark(policy=base_trained_policy, name=model_dir))
                except OSError as os_error:
                    logger.log(
                        f"Failed to load model from {model_dir}: {os_error}")

    return benchmarks


def scale_q_complete(q_complete: jnp.ndarray):
    """Scales the q_complete value. This is sigma from the MuZero Go paper."""
    return _QVAL_SCALE.value * q_complete


def get_value_model(go_model: hk.MultiTransformed,
                    params: optax.Params) -> ValueModel:
    """Returns value model function of the go model."""

    def value_fn(rng_key: jax.random.KeyArray,
                 states: jnp.ndarray) -> ValueOutput:
        embeds = go_model.apply[EMBED_INDEX](params, rng_key, states)
        value_logits = go_model.apply[VALUE_INDEX](params, rng_key, embeds)
        return ValueOutput(value=jax.nn.sigmoid(value_logits))

    return value_fn


# TODO: Extract this into a public function in the models.value module.
def _get_value_logits(final_area_logits: jnp.ndarray) -> jnp.ndarray:
    """Difference between sigmoid sum of the player's area and opponent's area."""
    chex.assert_rank(final_area_logits, 4)
    final_areas = jax.nn.sigmoid(final_area_logits)
    return jnp.sum(final_areas[:, 0], axis=(1, 2)) - jnp.sum(final_areas[:, 1],
                                                             axis=(1, 2))


def get_policy_model(go_model: hk.MultiTransformed,
                     params: optax.Params,
                     sample_action_size: int = 0) -> PolicyModel:
    """Returns policy model function of the go model.

    Args:
        go_model (hk.MultiTransformed): Go model.
        params (optax.Params): Parameters.
        sample_action_size (int): Sample action size at each tree level.
            `m` in the Gumbel MuZero paper.
    Returns:
        jax.tree_util.Partial: Policy model.
    """

    if sample_action_size <= 0:

        def policy_fn(rng_key: jax.random.KeyArray, states: jnp.ndarray):
            embeds = go_model.apply[EMBED_INDEX](params, rng_key, states)
            policy_logits = go_model.apply[POLICY_INDEX](params, rng_key,
                                                         embeds)
            gumbel = jax.random.gumbel(rng_key,
                                       shape=policy_logits.shape,
                                       dtype=policy_logits.dtype)
            return PolicyOutput(sampled_actions=jnp.argmax(
                policy_logits + gumbel, axis=-1).astype('uint16'),
                                visited_actions=None,
                                visited_qvalues=None)
    else:

        def policy_fn(rng_key: jax.random.KeyArray, states: jnp.ndarray):
            embeds = go_model.apply[EMBED_INDEX](params, rng_key, states)
            batch_size, hdim, board_size, _ = embeds.shape
            policy_logits = go_model.apply[POLICY_INDEX](params, rng_key,
                                                         embeds)
            gumbel = jax.random.gumbel(rng_key,
                                       shape=policy_logits.shape,
                                       dtype=policy_logits.dtype)
            sampled_logits_plus_gumbel, sampled_actions = jax.lax.top_k(
                policy_logits + gumbel, k=sample_action_size)
            chex.assert_shape(sampled_actions,
                              (batch_size, sample_action_size))
            # N x A' x D x B x B
            partial_transitions = go_model.apply[TRANSITION_INDEX](
                params, rng_key, embeds, batch_partial_actions=sampled_actions)
            chex.assert_shape(
                partial_transitions,
                (batch_size, sample_action_size, hdim, board_size, board_size))
            flattened_partial_transiion_final_area_logits = go_model.apply[
                VALUE_INDEX](
                    params, rng_key,
                    nt_utils.flatten_first_two_dims(partial_transitions))
            partial_transition_value_logits = nt_utils.unflatten_first_dim(
                _get_value_logits(
                    flattened_partial_transiion_final_area_logits), batch_size,
                sample_action_size)
            chex.assert_shape(partial_transition_value_logits,
                              (batch_size, sample_action_size))
            # We take the negative of the transition logits because they're in
            # the opponent's perspective.
            qvals = -partial_transition_value_logits
            argmax_of_top_m = jnp.argmax(sampled_logits_plus_gumbel +
                                         scale_q_complete(qvals),
                                         axis=1)
            return PolicyOutput(sampled_actions=sampled_actions[
                jnp.arange(len(sampled_actions)),
                argmax_of_top_m].astype('uint16'),
                                visited_actions=None,
                                visited_qvalues=None)

    return policy_fn


def save_model(params: optax.Params,
               all_models_build_config: _build_config.AllModelsBuildConfig,
               model_dir: str):
    """Saves the parameters and build config into the directory."""
    if not os.path.exists(model_dir):
        os.mkdir(model_dir)
    with open(os.path.join(model_dir, 'params.npz'), 'wb') as params_file:
        pickle.dump(jax.tree_map(lambda x: x.astype('float32'), params),
                    params_file)
    with open(os.path.join(model_dir, 'build_config.json'),
              'wt',
              encoding='utf-8') as build_config_file:
        json.dump(dataclasses.asdict(all_models_build_config),
                  build_config_file)
    logger.log(f"Saved model to '{model_dir}'.")
