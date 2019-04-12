# -*- coding: utf-8 -*-
# @Time    : 2019/4/10 10:38
# @Author  : Alan
# @Email   : xiezhengwen2013@163.com
# @File    : model_elmo.py
# @Software: PyCharm

import tensorflow as tf
from model_utils import *
import tensorflow_hub as hub
import time


class SiameseQACNN_elmo(object):
    def __init__(self, config):
        self.ques_len = config.ques_length
        self.ans_len = config.ans_length
        self.hidden_size = config.hidden_size
        self.output_size = config.output_size
        self.pos_weight = config.pos_weight
        self.learning_rate = config.learning_rate
        self.optimizer = config.optimizer
        self.l2_lambda = config.l2_lambda
        self.clip_value = config.clip_value
        self.embeddings = config.embeddings
        self.window_sizes = config.window_sizes
        self.n_filters = config.n_filters
        self.margin = config.margin
        self.embedding_size = config.embedding_size

        self._placeholder_init_pointwise()
        self.q_a_cosine, self.q_aneg_cosine = self._build()
        # 损失和精确度
        self.total_loss, self.accu = self._add_loss_op(self.q_a_cosine, self.q_aneg_cosine, self.l2_lambda)
        # 训练节点
        self.train_op = self._add_train_op(self.total_loss)

    def _placeholder_init_pointwise(self):
        self._ques = tf.placeholder(tf.float32, [None, self.ques_len, self.embedding_size], name='ques_point')
        self._ans = tf.placeholder(tf.float32, [None, self.ans_len, self.embedding_size], name='ans_point')
        self._ans_neg = tf.placeholder(tf.float32, [None, self.ans_len, self.embedding_size], name='ans_point')
        self._ques_mask = tf.placeholder(tf.int32, [None], 'ques_mask')
        self._ans_mask = tf.placeholder(tf.int32, [None], 'ans_mask')
        self._ans_mask_neg = tf.placeholder(tf.int32, [None], 'ans_mask_neg')
        self.dropout_keep_prob = tf.placeholder(tf.float32, name="dropout_keep_prob")
        self.batch_size, self.list_size = tf.shape(self._ans)[0], tf.shape(self._ans)[1]

    def _HL_layer(self, bottom, n_weight, name):
        """
        全连接层
        """
        assert len(bottom.get_shape()) == 3
        n_prev_weight = bottom.get_shape()[-1]
        max_len = bottom.get_shape()[1]
        initer = tf.truncated_normal_initializer(stddev=0.01)
        W = tf.get_variable(name + 'W', dtype=tf.float32, shape=[n_prev_weight, n_weight],
                            initializer=tf.uniform_unit_scaling_initializer())
        b = tf.get_variable(name + 'b', dtype=tf.float32,
                            initializer=tf.constant(0.1, shape=[n_weight], dtype=tf.float32))
        bottom_2 = tf.reshape(bottom, [-1, n_prev_weight])
        hl = tf.nn.bias_add(tf.matmul(bottom_2, W), b)
        hl_tanh = tf.nn.tanh(hl)
        HL = tf.reshape(hl_tanh, [-1, max_len, n_weight])
        return HL

    def fc_layer(self, bottom, n_weight, name):
        """
        全连接层
        """
        assert len(bottom.get_shape()) == 2
        n_prev_weight = bottom.get_shape()[1]
        initer = tf.truncated_normal_initializer(stddev=0.01)
        W = tf.get_variable(name + 'W', dtype=tf.float32, shape=[n_prev_weight, n_weight], initializer=initer)
        b = tf.get_variable(name + 'b', dtype=tf.float32,
                            initializer=tf.constant(0.01, shape=[n_weight], dtype=tf.float32))
        fc = tf.nn.bias_add(tf.matmul(bottom, W), b)
        return fc

    def _network(self, x):
        """
         核心网络
        """
        fc1 = self.fc_layer(x, self.hidden_size, "fc1")
        ac1 = tf.nn.relu(fc1)
        fc2 = self.fc_layer(ac1, self.hidden_size, "fc2")
        return fc2

    def _cnn_layer(self, input):
        """
        卷积层
        """
        all = []
        max_len = input.get_shape()[1]
        for i, filter_size in enumerate(self.window_sizes):
            with tf.variable_scope('filter{}'.format(filter_size)):
                # 卷积
                cnn_out = tf.layers.conv1d(input, self.n_filters, filter_size, padding='valid',
                                           activation=tf.nn.relu, name='q_conv_' + str(i))
                # 池化
                pool_out = tf.reduce_max(cnn_out, axis=1, keepdims=True)
                tanh_out = tf.nn.tanh(pool_out)
                all.append(tanh_out)
        cnn_outs = tf.concat(all, axis=-1)
        dim = cnn_outs.get_shape()[-1]
        cnn_outs = tf.reshape(cnn_outs, [-1, dim])
        return cnn_outs

    def _build(self):
        q_mask = tf.sequence_mask(self._ques_mask, self.ques_len, dtype=tf.float32)
        a_mask = tf.sequence_mask(self._ans_mask, self.ans_len, dtype=tf.float32)
        a_mask_neg = tf.sequence_mask(self._ans_mask_neg, self.ans_len, dtype=tf.float32)

        # with tf.name_scope("embedding"):
        #     embeddingW1 = tf.get_variable(
        #         "embeddingW1",
        #         shape=[self.embedding_size, self.embedding_size],
        #         initializer=tf.contrib.layers.xavier_initializer())
        #     embeddingW2 = tf.get_variable(
        #         "embeddingW2",
        #         shape=[self.embedding_size, self.embedding_size],
        #         initializer=tf.contrib.layers.xavier_initializer())
        #     embeddingW3 = tf.get_variable(
        #         "embeddingW3",
        #         shape=[self.embedding_size, self.embedding_size],
        #         initializer=tf.contrib.layers.xavier_initializer())
        #
        #     self.q_embed = tf.nn.dropout(multiply_3_2(self._ques, embeddingW1), self.dropout_keep_prob)
        #     self.a_embed = tf.nn.dropout(multiply_3_2(self._ans, embeddingW2), self.dropout_keep_prob)
        #     self.a_neg_embed = tf.nn.dropout(multiply_3_2(self._ans_neg, embeddingW3), self.dropout_keep_prob)
        self.q_embed = tf.nn.dropout(self._ques, self.dropout_keep_prob)
        self.a_embed = tf.nn.dropout(self._ans, self.dropout_keep_prob)
        self.a_neg_embed = tf.nn.dropout(self._ans_neg, self.dropout_keep_prob)

        with tf.variable_scope('siamese') as scope:
            # 计算隐藏和卷积层
            hl_q = self._HL_layer(self.q_embed, self.hidden_size, 'HL_layer')
            conv1_q = self._cnn_layer(hl_q)
            scope.reuse_variables()
            hl_a = self._HL_layer(self.a_embed, self.hidden_size, 'HL_layer')
            hl_a_neg = self._HL_layer(self.a_neg_embed, self.hidden_size, 'HL_layer')
            conv1_a = self._cnn_layer(hl_a)
            conv1_a_neg = self._cnn_layer(hl_a_neg)

            # 计算余弦相似度
            # q_a_cosine = feature2cos_sim(tf.nn.l2_normalize(conv1_q, dim=1), tf.nn.l2_normalize(conv1_a, dim=1))
            # q_aneg_cosine = feature2cos_sim(tf.nn.l2_normalize(conv1_q, dim=1), tf.nn.l2_normalize(conv1_a_neg, dim=1))
            q_a_cosine = tf.reduce_sum(tf.multiply(tf.nn.l2_normalize(conv1_q, dim=1), tf.nn.l2_normalize(conv1_a, dim=1)), 1)
            q_aneg_cosine = tf.reduce_sum(tf.multiply(tf.nn.l2_normalize(conv1_q, dim=1), tf.nn.l2_normalize(conv1_a_neg, dim=1)), 1)
            return q_a_cosine, q_aneg_cosine

    def _margin_loss(self, pos_sim, neg_sim):
        original_loss = self.margin - pos_sim + neg_sim
        l = tf.maximum(tf.zeros_like(original_loss), original_loss)
        loss = tf.reduce_sum(l)
        return loss, l

    def _add_loss_op(self, p_sim, n_sim, l2_lambda=0.0001):
        """
        损失节点
        """
        loss, l = self._margin_loss(p_sim, n_sim)
        accu = tf.reduce_mean(tf.cast(tf.equal(0., l), tf.float32))
        reg_losses = tf.get_collection(tf.GraphKeys.REGULARIZATION_LOSSES)
        l2_loss = sum(reg_losses) * l2_lambda
        pairwise_loss = loss + l2_loss
        tf.summary.scalar('pairwise_loss', pairwise_loss)
        return pairwise_loss, accu

    def _add_train_op(self, loss):
        """
        训练节点
        """
        with tf.name_scope('train_op'):
            # 记录训练步骤
            self.global_step = tf.Variable(0, name='global_step', trainable=False)
            opt = tf.train.AdamOptimizer(self.learning_rate)
            # train_op = opt.minimize(loss, self.global_step)
            # 计算梯度,得到梯度和变量
            gradsAndVars = opt.compute_gradients(loss)
            # 将梯度应用到变量下，生成训练器
            train_op = opt.apply_gradients(gradsAndVars, global_step=self.global_step)

            # 用summary绘制tensorBoard
            for g, v in gradsAndVars:
                if g is not None:
                    tf.summary.histogram("{}/grad/hist".format(v.name), g)
                    tf.summary.scalar("{}/grad/sparsity".format(v.name), tf.nn.zero_fraction(g))
            self.summary_op = tf.summary.merge_all()
            return train_op
