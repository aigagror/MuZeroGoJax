"""Models that try to map Go embeddings back to their states."""

import gojax
import haiku as hk
import jax.numpy as jnp

from muzero_gojax.models import base


class AmplifiedDecode(base.BaseGoModel):
    """Amplifies the logit values."""

    def __call__(self, embeds: jnp.ndarray) -> jnp.ndarray:
        # Return the embeds transformed by (x * 200 - 100) if the embeds have the same shape as Go
        # states, which likely means that the embeds ARE the Go states. If they are the Go states,
        # then the values should be {-100, 100}, and the loss should be nearly 0 and the accuracy
        # perfect.
        if embeds.shape[1:] == (gojax.NUM_CHANNELS,
                                self.model_config.board_size,
                                self.model_config.board_size):
            return embeds.astype(self.model_config.dtype) * 200 - 100
        # Otherwise return an empty batch of Go states with the proper shape.
        return gojax.new_states(board_size=embeds.shape[2],
                                batch_size=len(embeds)).astype(
                                    self.model_config.dtype)


class ScaleDecode(base.BaseGoModel):
    """
    Scales the logit values with a large value.

    Preserves the postive / negative of the values.
    """

    def __call__(self, embeds: jnp.ndarray) -> jnp.ndarray:
        # Return the embeds transformed by (x * 200 - 100) if the embeds have the same shape as Go
        # states, which likely means that the embeds ARE the Go states. If they are the Go states,
        # then the values should be {-100, 100}, and the loss should be nearly 0 and the accuracy
        # perfect.
        if embeds.shape[1:] == (gojax.NUM_CHANNELS,
                                self.model_config.board_size,
                                self.model_config.board_size):
            return embeds.astype(self.model_config.dtype) * 200
        # Otherwise return an empty batch of Go states with the proper shape.
        return gojax.new_states(board_size=embeds.shape[2],
                                batch_size=len(embeds)).astype(
                                    self.model_config.dtype)


class NonSpatialConvDecode(base.BaseGoModel):
    """Linear convolution model."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._conv = base.NonSpatialConv(hdim=self.model_config.hdim,
                                         odim=gojax.NUM_CHANNELS,
                                         nlayers=0)

    def __call__(self, embeds):
        embeds = embeds.astype(self.model_config.dtype)
        return self._conv(embeds.astype(self.model_config.dtype))


class ResNetV2Decode(base.BaseGoModel):
    """ResNetV2 model."""

    def __init__(self, *args, **kwargs):
        # pylint: disable=duplicate-code
        super().__init__(*args, **kwargs)
        self._resnet = base.ResNetV2(hdim=self.model_config.hdim,
                                     nlayers=self.submodel_config.nlayers,
                                     odim=self.model_config.hdim)
        self._conv = hk.Conv2D(gojax.NUM_CHANNELS, (1, 1), data_format='NCHW')

    def __call__(self, embeds: jnp.ndarray) -> jnp.ndarray:
        return self._conv(self._resnet(embeds.astype(self.model_config.dtype)))
