"""Algorithms for spectral clustering"""

# Author: Gael Varoquaux gael.varoquaux@normalesup.org
#         Brian Cheung
#         Wei LI <kuantkid@gmail.com>
# License: BSD
import warnings

import numpy as np

from ..base import BaseEstimator, ClusterMixin
from ..utils import check_random_state, as_float_array, deprecated
from ..utils.extmath import norm
from ..metrics.pairwise import pairwise_kernels
from ..neighbors import kneighbors_graph
from ..manifold import spectral_embedding
from .k_means_ import k_means


def discretize(vectors, copy=True, max_svd_restarts=30, n_iter_max=20,
               random_state=None):
    """Search for a partition matrix (clustering) which is closest to the
    eigenvector embedding.

    Parameters
    ----------
    vectors : array-like, shape: (n_samples, n_clusters)
        The embedding space of the samples.

    copy : boolean, optional, default: True
        Whether to copy vectors, or perform in-place normalization.

    max_svd_restarts : int, optional, default: 30
        Maximum number of attempts to restart SVD if convergence fails

    n_iter_max : int, optional, default: 30
        Maximum number of iterations to attempt in rotation and partition
        matrix search if machine precision convergence is not reached

    random_state: int seed, RandomState instance, or None (default)
        A pseudo random number generator used for the initialization of the
        of the rotation matrix

    Returns
    -------
    labels : array of integers, shape: n_samples
        The labels of the clusters.

    References
    ----------

    - Multiclass spectral clustering, 2003
      Stella X. Yu, Jianbo Shi
      http://www1.icsi.berkeley.edu/~stellayu/publication/doc/2003kwayICCV.pdf

    Notes
    -----

    The eigenvector embedding is used to iteratively search for the
    closest discrete partition.  First, the eigenvector embedding is
    normalized to the space of partition matrices. An optimal discrete
    partition matrix closest to this normalized embedding multiplied by
    an initial rotation is calculated.  Fixing this discrete partition
    matrix, an optimal rotation matrix is calculated.  These two
    calculations are performed until convergence.  The discrete partition
    matrix is returned as the clustering solution.  Used in spectral
    clustering, this method tends to be faster and more robust to random
    initialization than k-means.

    """

    from scipy.sparse import csc_matrix
    from scipy.linalg import LinAlgError

    random_state = check_random_state(random_state)

    vectors = as_float_array(vectors, copy=copy)

    eps = np.finfo(float).eps
    n_samples, n_components = vectors.shape

    # Normalize the eigenvectors to an equal length of a vector of ones.
    # Reorient the eigenvectors to point in the negative direction with respect
    # to the first element.  This may have to do with constraining the
    # eigenvectors to lie in a specific quadrant to make the discretization
    # search easier.
    norm_ones = np.sqrt(n_samples)
    for i in range(vectors.shape[1]):
        vectors[:, i] = (vectors[:, i] / norm(vectors[:, i])) \
            * norm_ones
        if vectors[0, i] != 0:
            vectors[:, i] = -1 * vectors[:, i] * np.sign(vectors[0, i])

    # Normalize the rows of the eigenvectors.  Samples should lie on the unit
    # hypersphere centered at the origin.  This transforms the samples in the
    # embedding space to the space of partition matrices.
    vectors = vectors / np.sqrt((vectors ** 2).sum(axis=1))[:, np.newaxis]

    svd_restarts = 0
    has_converged = False

    # If there is an exception we try to randomize and rerun SVD again
    # do this max_svd_restarts times.
    while (svd_restarts < max_svd_restarts) and not has_converged:

        # Initialize first column of rotation matrix with a row of the
        # eigenvectors
        rotation = np.zeros((n_components, n_components))
        rotation[:, 0] = vectors[random_state.randint(n_samples), :].T

        # To initialize the rest of the rotation matrix, find the rows
        # of the eigenvectors that are as orthogonal to each other as
        # possible
        c = np.zeros(n_samples)
        for j in range(1, n_components):
            # Accumulate c to ensure row is as orthogonal as possible to
            # previous picks as well as current one
            c += np.abs(np.dot(vectors, rotation[:, j - 1]))
            rotation[:, j] = vectors[c.argmin(), :].T

        last_objective_value = 0.0
        n_iter = 0

        while not has_converged:
            n_iter += 1

            t_discrete = np.dot(vectors, rotation)

            labels = t_discrete.argmax(axis=1)
            vectors_discrete = csc_matrix(
                (np.ones(len(labels)), (np.arange(0, n_samples), labels)),
                shape=(n_samples, n_components))

            t_svd = vectors_discrete.T * vectors

            try:
                U, S, Vh = np.linalg.svd(t_svd)
                svd_restarts += 1
            except LinAlgError:
                print("SVD did not converge, randomizing and trying again")
                break

            ncut_value = 2.0 * (n_samples - S.sum())
            if ((abs(ncut_value - last_objective_value) < eps) or
               (n_iter > n_iter_max)):
                has_converged = True
            else:
                # otherwise calculate rotation and continue
                last_objective_value = ncut_value
                rotation = np.dot(Vh.T, U.T)

    if not has_converged:
        raise LinAlgError('SVD did not converge')
    return labels


def spectral_clustering(affinity, n_clusters=8, n_components=None,
                        eigen_solver=None, random_state=None, n_init=10,
                        k=None, eigen_tol=0.0,
                        assign_labels='kmeans',
                        mode=None):
    """Apply clustering to a projection to the normalized laplacian.

    In practice Spectral Clustering is very useful when the structure of
    the individual clusters is highly non-convex or more generally when
    a measure of the center and spread of the cluster is not a suitable
    description of the complete cluster. For instance when clusters are
    nested circles on the 2D plan.

    If affinity is the adjacency matrix of a graph, this method can be
    used to find normalized graph cuts.

    Parameters
    -----------
    affinity: array-like or sparse matrix, shape: (n_samples, n_samples)
        The affinity matrix describing the relationship of the samples to
        embed. **Must be symetric**.

        Possible examples:
          - adjacency matrix of a graph,
          - heat kernel of the pairwise distance matrix of the samples,
          - symmetic k-nearest neighbours connectivity matrix of the samples.

    n_clusters: integer, optional
        Number of clusters to extract.

    n_components: integer, optional, default is k
        Number of eigen vectors to use for the spectral embedding

    eigen_solver: {None, 'arpack' or 'amg'}
        The eigenvalue decomposition strategy to use. AMG requires pyamg
        to be installed. It can be faster on very large, sparse problems,
        but may also lead to instabilities

    random_state: int seed, RandomState instance, or None (default)
        A pseudo random number generator used for the initialization
        of the lobpcg eigen vectors decomposition when eigen_solver == 'amg'
        and by the K-Means initialization.

    n_init: int, optional, default: 10
        Number of time the k-means algorithm will be run with different
        centroid seeds. The final results will be the best output of
        n_init consecutive runs in terms of inertia.

    eigen_tol : float, optional, default: 0.0
        Stopping criterion for eigendecomposition of the Laplacian matrix
        when using arpack eigen_solver.

    assign_labels : {'kmeans', 'discretize'}, default: 'kmeans'
        The strategy to use to assign labels in the embedding
        space.  There are two ways to assign labels after the laplacian
        embedding.  k-means can be applied and is a popular choice. But it can
        also be sensitive to initialization. Discretization is another
        approach which is less sensitive to random initialization. See
        the 'Multiclass spectral clustering' paper referenced below for
        more details on the discretization approach.

    Returns
    -------
    labels: array of integers, shape: n_samples
        The labels of the clusters.

    References
    ----------

    - Normalized cuts and image segmentation, 2000
      Jianbo Shi, Jitendra Malik
      http://citeseer.ist.psu.edu/viewdoc/summary?doi=10.1.1.160.2324

    - A Tutorial on Spectral Clustering, 2007
      Ulrike von Luxburg
      http://citeseerx.ist.psu.edu/viewdoc/summary?doi=10.1.1.165.9323

    - Multiclass spectral clustering, 2003
      Stella X. Yu, Jianbo Shi
      http://www1.icsi.berkeley.edu/~stellayu/publication/doc/2003kwayICCV.pdf

    Notes
    ------
    The graph should contain only one connect component, elsewhere
    the results make little sense.

    This algorithm solves the normalized cut for k=2: it is a
    normalized spectral clustering.
    """
    if not assign_labels in ('kmeans', 'discretize'):
        raise ValueError("The 'assign_labels' parameter should be "
                         "'kmeans' or 'discretize', but '%s' was given"
                         % assign_labels)

    if not k is None:
        warnings.warn("'k' was renamed to n_clusters and will "
                      "be removed in 0.15.",
                      DeprecationWarning)
        n_clusters = k
    if not mode is None:
        warnings.warn("'mode' was renamed to eigen_solver "
                      "and will be removed in 0.15.",
                      DeprecationWarning)
        eigen_solver = mode

    random_state = check_random_state(random_state)
    n_components = n_clusters if n_components is None else n_components
    maps = spectral_embedding(affinity, n_components=n_components,
                              eigen_solver=eigen_solver,
                              random_state=random_state,
                              eigen_tol=eigen_tol, drop_first=False)

    if assign_labels == 'kmeans':
        _, labels, _ = k_means(maps, n_clusters, random_state=random_state,
                               n_init=n_init)
    else:
        labels = discretize(maps, random_state=random_state)

    return labels


class SpectralClustering(BaseEstimator, ClusterMixin):
    """Apply clustering to a projection to the normalized laplacian.

    In practice Spectral Clustering is very useful when the structure of
    the individual clusters is highly non-convex or more generally when
    a measure of the center and spread of the cluster is not a suitable
    description of the complete cluster. For instance when clusters are
    nested circles on the 2D plan.

    If affinity is the adjacency matrix of a graph, this method can be
    used to find normalized graph cuts.

    When calling ``fit``, an affinity matrix is constructed using either the
    Gaussian (aka RBF) kernel of the euclidean distanced ``d(X, X)``::

            np.exp(-gamma * d(X,X) ** 2)

    or a k-nearest neighbors connectivity matrix.

    Alternatively, using ``precomputed``, a user-provided affinity
    matrix can be used.

    Parameters
    -----------
    n_clusters : integer, optional
        The dimension of the projection subspace.

    affinity: string, 'nearest_neighbors', 'precomputed', or a kernel:
        'linear' | 'poly' | 'rbf' | 'sigmoid' | 'cosine'
        Default: 'rbf'

    gamma: float
        Scaling factor of Gaussian (rbf) affinity kernel. Ignored for
        ``affinity='nearest_neighbors'``.

    n_neighbors: integer
        Number of neighbors to use when constructing the affinity matrix using
        the nearest neighbors method. Ignored for ``affinity='rbf'``.

    eigen_solver: {None, 'arpack' or 'amg'}
        The eigenvalue decomposition strategy to use. AMG requires pyamg
        to be installed. It can be faster on very large, sparse problems,
        but may also lead to instabilities

    random_state : int seed, RandomState instance, or None (default)
        A pseudo random number generator used for the initialization
        of the lobpcg eigen vectors decomposition when eigen_solver == 'amg'
        and by the K-Means initialization.

    n_init : int, optional, default: 10
        Number of time the k-means algorithm will be run with different
        centroid seeds. The final results will be the best output of
        n_init consecutive runs in terms of inertia.

    eigen_tol : float, optional, default: 0.0
        Stopping criterion for eigendecomposition of the Laplacian matrix
        when using arpack eigen_solver.

    assign_labels : {'kmeans', 'discretize'}, default: 'kmeans'
        The strategy to use to assign labels in the embedding
        space. There are two ways to assign labels after the laplacian
        embedding. k-means can be applied and is a popular choice. But it can
        also be sensitive to initialization. Discretization is another approach
        which is less sensitive to random initialization.

    kernel_params : mapping of string to any, optional
        Parameters (keyword arguments) and values for kernel passed as
        callable object. Ignored by other kernels.

    Attributes
    ----------
    `affinity_matrix_` : array-like, shape (n_samples, n_samples)
        Affinity matrix used for clustering. Available only if after calling
        ``fit``.

    `labels_` :
        Labels of each point

    Notes
    -----
    If you have an affinity matrix, such as a distance matrix,
    for which 0 means identical elements, and high values means
    very dissimilar elements, it can be transformed in a
    similarity matrix that is well suited for the algorithm by
    applying the Gaussian (RBF, heat) kernel::

        np.exp(- X ** 2 / (2. * delta ** 2))

    Another alternative is to take a symmetric version of the k
    nearest neighbors connectivity matrix of the points.

    If the pyamg package is installed, it is used: this greatly
    speeds up computation.

    References
    ----------

    - Normalized cuts and image segmentation, 2000
      Jianbo Shi, Jitendra Malik
      http://citeseer.ist.psu.edu/viewdoc/summary?doi=10.1.1.160.2324

    - A Tutorial on Spectral Clustering, 2007
      Ulrike von Luxburg
      http://citeseerx.ist.psu.edu/viewdoc/summary?doi=10.1.1.165.9323

    - Multiclass spectral clustering, 2003
      Stella X. Yu, Jianbo Shi
      http://www1.icsi.berkeley.edu/~stellayu/publication/doc/2003kwayICCV.pdf
    """

    def __init__(self, n_clusters=8, eigen_solver=None, random_state=None,
                 n_init=10, gamma=1., affinity='rbf', n_neighbors=10, k=None,
                 eigen_tol=0.0, assign_labels='kmeans', mode=None, kernel='rbf',
                 kernel_params=None):
        if k is not None:
            warnings.warn("'k' was renamed to n_clusters and "
                          "will be removed in 0.15.",
                          DeprecationWarning)
            n_clusters = k
        if mode is not None:
            warnings.warn("'mode' was renamed to eigen_solver and "
                          "will be removed in 0.15.",
                          DeprecationWarning)
            eigen_solver = mode

        self.n_clusters = n_clusters
        self.eigen_solver = eigen_solver
        self.random_state = random_state
        self.n_init = n_init
        self.gamma = gamma
        self.affinity = affinity
        self.n_neighbors = n_neighbors
        self.eigen_tol = eigen_tol
        self.assign_labels = assign_labels
        self.kernel = kernel
        self.kernel_params = kernel_params

    def fit(self, X):
        """Creates an affinity matrix for X using the selected affinity,
        then applies spectral clustering to this affinity matrix.

        Parameters
        ----------
        X : array-like or sparse matrix, shape (n_samples, n_features)
            OR, if affinity==`precomputed`, a precomputed affinity
            matrix of shape (n_samples, n_samples)
        """
        if X.shape[0] == X.shape[1] and self.affinity != "precomputed":
            warnings.warn("The spectral clustering API has changed. ``fit``"
                          "now constructs an affinity matrix from data. To use"
                          " a custom affinity matrix, "
                          "set ``affinity=precomputed``.")

        if self.affinity == 'nearest_neighbors':
            connectivity = kneighbors_graph(X, n_neighbors=self.n_neighbors)
            self.affinity_matrix_ = 0.5 * (connectivity + connectivity.T)
        elif self.affinity == 'precomputed':
            self.affinity_matrix_ = X
        else:
            self.affinity_matrix_ = pairwise_kernels(X, metric=self.affinity,
                                                     filter_params=True,
                                                     **self._get_kernel_params())

        random_state = check_random_state(self.random_state)
        self.labels_ = spectral_clustering(self.affinity_matrix_,
                                           n_clusters=self.n_clusters,
                                           eigen_solver=self.eigen_solver,
                                           random_state=random_state,
                                           n_init=self.n_init,
                                           eigen_tol=self.eigen_tol,
                                           assign_labels=self.assign_labels)
        return self

    @property
    def _pairwise(self):
        return self.affinity == "precomputed"

    @property
    @deprecated("'mode' was renamed to eigen_solver and will be removed in"
                " 0.15.")
    def mode(self):
        return self.eigen_solver

    @property
    @deprecated("'k' was renamed to n_clusters and will be removed in"
                " 0.15.")
    def k(self):
        return self.n_clusters

    def _get_kernel_params(self):
        params = self.kernel_params
        if params is None:
            params = {}
        if not callable(self.kernel):
            params['gamma'] = self.gamma
        return params