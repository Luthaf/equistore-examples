import numpy as np
from math import sqrt

from equistore import Labels, TensorBlock, TensorMap
from equistore.operations import slice_block

import utils.models.operations as ops


def compute_power_spectrum(spherical_expansion_1, spherical_expansion_2=None):
    """
    Starting from spherical expansion coefficients obtained from an
    external code (e.g. rascaline or also librascal/pyLODE after converting
    to the proper storage format) generate the rotationally invariant
    power spectrum.
    Returns:
    --------
    A Descriptor object containing the invariant features and all its
    associated labels.
    """
    # Make sure that the expansion coefficients have the correct set of keys
    # associated with 1-center expansion coefficients.
    assert spherical_expansion_1.keys.names == (
        "spherical_harmonics_l",
        "species_center",
        "species_neighbor",
    )

    # If two different sets of coefficients are provided, it is assumed
    # that the invariants are generated by taking quadratic combinations
    # of those.
    # Otherwise, invariants are generated by combining coefficients from
    # two different calculators.
    if spherical_expansion_2 is None:
        use_same_spherical_expansions = True
        spherical_expansion_2 = spherical_expansion_1
    else:
        use_same_spherical_expansions = False
        assert spherical_expansion_2.keys.names == (
            "spherical_harmonics_l",
            "species_center",
            "species_neighbor",
        )

    blocks = []
    keys = []

    for (l1, cs1, ns1), spx_1 in spherical_expansion_1:
        for (l2, cs2, ns2), spx_2 in spherical_expansion_2:
            if l1 != l2 or cs1 != cs2:
                continue

            # Find common samples if samples are not the same
            if not np.all(spx_1.samples == spx_2.samples):
                common_samples = Labels(
                    names=spx_1.samples.names,
                    values=np.asarray(
                        np.intersect1d(spx_1.samples, spx_2.samples).tolist()))

                spx_1 = slice_block(spx_1, samples=common_samples)
                spx_2 = slice_block(spx_2, samples=common_samples)

            # Avoid doubly computing / storing invariants that are
            # the same by symmetry of the neighbor species.
            # Example: Neighbor species (Na, Cl) produces the same
            # invariants as (Cl, Na), meaning that only one set
            # of invariants needs to be used.
            # If the two sets of expansion coefficients are different,
            # this does not apply
            if ns1 > ns2 and use_same_spherical_expansions:
                continue
            elif ns1 == ns2:
                factor = 1.0 / sqrt(2 * l1 + 1)
            else:
                factor = sqrt(2) / sqrt(2 * l1 + 1)

            properties = Labels(
                names=[f"{name}_1" for name in spx_1.properties.names]
                + [f"{name}_2" for name in spx_2.properties.names],
                values=np.array(
                    [
                        properties_1.tolist() + properties_2.tolist()
                        for properties_1 in spx_1.properties
                        for properties_2 in spx_2.properties
                    ],
                    dtype=np.int32,
                ),
            )

            # Compute the invariants by summation and store the results
            data = factor * ops.einsum("ima, imb -> iab", spx_1.values, spx_2.values)

            block = TensorBlock(
                values=data.reshape(data.shape[0], -1),
                samples=spx_1.samples,
                components=[],
                properties=properties,
            )

            n_properties = block.values.shape[1]

            if spx_1.has_gradient("positions"):
                gradient_1 = spx_1.gradient("positions")
                gradient_2 = spx_2.gradient("positions")

                if len(gradient_1.samples) == 0 or len(gradient_2.samples) == 0:
                    continue

                gradients_samples = np.unique(
                    np.concatenate([gradient_1.samples, gradient_2.samples])
                )
                gradients_samples = gradients_samples.view(np.int32).reshape(-1, 3)

                gradients_samples = Labels(
                    names=gradient_1.samples.names, values=gradients_samples
                )

                gradients_sample_mapping = {
                    tuple(sample): i for i, sample in enumerate(gradients_samples)
                }

                gradient_data = ops.zeros_like(
                    gradient_1.data, (gradients_samples.shape[0], 3, n_properties)
                )

                gradient_data_1 = factor * ops.einsum(
                    "ixma, imb -> ixab",
                    gradient_1.data,
                    spx_2.values[gradient_1.samples["sample"], :, :],
                ).reshape(gradient_1.samples.shape[0], 3, -1)

                for sample, row in zip(gradient_1.samples, gradient_data_1):
                    new_row = gradients_sample_mapping[tuple(sample)]
                    gradient_data[new_row, :, :] += row

                gradient_data_2 = factor * ops.einsum(
                    "ima, ixmb -> ixab",
                    spx_1.values[gradient_2.samples["sample"], :, :],
                    gradient_2.data,
                ).reshape(gradient_2.samples.shape[0], 3, -1)

                for sample, row in zip(gradient_2.samples, gradient_data_2):
                    new_row = gradients_sample_mapping[tuple(sample)]
                    gradient_data[new_row, :, :] += row

                assert gradient_1.components[0].names == ("direction",)
                block.add_gradient(
                    "positions",
                    gradient_data,
                    gradients_samples,
                    [gradient_1.components[0]],
                )

            keys.append((l1, cs1, ns1, ns2))
            blocks.append(block)

    keys = Labels(
        names=[
            "spherical_harmonics_l",
            "species_center",
            "species_neighbor_1",
            "species_neighbor_2",
        ],
        values=np.array(keys, dtype=np.int32),
    )
    descriptor = TensorMap(keys, blocks)
    descriptor.keys_to_properties("spherical_harmonics_l")
    return descriptor
