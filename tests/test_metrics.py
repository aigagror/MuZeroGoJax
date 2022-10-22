"""Tests metric functions."""
# pylint: disable=duplicate-code
import tempfile

import gojax
import jax.numpy as jnp
import jax.random
import matplotlib.pyplot as plt
import numpy as np
import pandas
from PIL import Image
from absl.testing import absltest
from absl.testing import flagsaver

from muzero_gojax import game
from muzero_gojax import main
from muzero_gojax import metrics
from muzero_gojax import models

FLAGS = main.FLAGS


class MetricsTest(absltest.TestCase):
    """Tests metrics.py."""

    def setUp(self):
        FLAGS.mark_as_parsed()

    def test_plot_histogram_weights_matches_golden_image(self):
        """Tests histogram plot."""
        params = {
            'foo': {
                'w': jax.random.normal(jax.random.PRNGKey(1), (2, 2), dtype='bfloat16'),
                'b': jax.random.normal(jax.random.PRNGKey(2), (2, 2), dtype='bfloat16')
            }, 'bar': {'w': jax.random.normal(jax.random.PRNGKey(3), (2, 2), dtype='bfloat16')}
        }
        metrics.plot_histogram_weights(params)

        with tempfile.TemporaryFile() as file_pointer:
            plt.savefig(file_pointer)
            # Uncomment line below to update golden image.
            # plt.savefig('tests/test_data/histogram_weights_golden.png')
            file_pointer.seek(0)
            test_image = jnp.asarray(Image.open(file_pointer))
            expected_image = jnp.asarray(Image.open('tests/test_data/histogram_weights_golden.png'))
            diff_image = jnp.abs(test_image - expected_image)
            np.testing.assert_array_equal(diff_image, jnp.zeros_like(diff_image))

    def test_plot_trajectories_matches_golden_image(self):
        """Tests trajectories plot."""
        trajectories = game.Trajectories(nt_states=jnp.reshape(gojax.decode_states("""
                            _ _ _
                            _ _ _
                            _ _ _
    
                            _ _ _
                            _ _ B
                            _ _ _
                            TURN=W
                            
                            B _ _
                            W _ _
                            _ _ _
                            TURN=W
    
                            B _ _
                            W _ _
                            _ _ _
                            PASS=T
                            """), (2, 2, 6, 3, 3)),
                                         nt_actions=jnp.array([[5, -1], [9, -1]], dtype='uint16'))
        metrics.plot_trajectories(trajectories)
        with tempfile.TemporaryFile() as file_pointer:
            plt.savefig(file_pointer)
            # Uncomment line below to update golden image.
            # plt.savefig('tests/test_data/trajectory_golden.png')
            file_pointer.seek(0)
            test_image = jnp.asarray(Image.open(file_pointer))
            expected_image = jnp.asarray(Image.open('tests/test_data/trajectory_golden.png'))
            diff_image = jnp.abs(test_image - expected_image)
            np.testing.assert_array_equal(diff_image, jnp.zeros_like(diff_image))

    @flagsaver.flagsaver(board_size=4, hdim=2, embed_model='linear_conv', value_model='linear_conv',
                         policy_model='linear_conv', transition_model='linear_conv')
    def test_plot_model_thoughts_on_interesting_states_matches_golden_image(self):
        """Tests model_thoughts plot."""
        go_model, params = models.make_model(main.FLAGS.board_size)
        states = metrics.get_interesting_states(board_size=4)
        metrics.plot_model_thoughts(go_model, params, states)

        with tempfile.TemporaryFile() as file_pointer:
            plt.savefig(file_pointer)
            # Uncomment line below to update golden image.
            # plt.savefig('tests/test_data/model_thoughts_golden.png')
            file_pointer.seek(0)
            test_image = jnp.asarray(Image.open(file_pointer))
            expected_image = jnp.asarray(Image.open('tests/test_data/model_thoughts_golden.png'))
            diff_image = jnp.abs(test_image - expected_image)
            np.testing.assert_array_equal(diff_image, jnp.zeros_like(diff_image))

    def test_plot_metrics_matches_golden_image(self):
        """Tests metrics plot."""
        metrics_df = pandas.DataFrame({'foo': [0, 1, 2], 'bar': [-1, 1, -1]})
        metrics.plot_metrics(metrics_df)

        with tempfile.TemporaryFile() as file_pointer:
            plt.savefig(file_pointer)
            # Uncomment line below to update golden image.
            # plt.savefig('tests/test_data/metrics_golden.png')
            file_pointer.seek(0)
            test_image = jnp.asarray(Image.open(file_pointer))
            expected_image = jnp.asarray(Image.open('tests/test_data/metrics_golden.png'))
            diff_image = jnp.abs(test_image - expected_image)
            np.testing.assert_array_equal(diff_image, jnp.zeros_like(diff_image))


if __name__ == '__main__':
    absltest.main()
