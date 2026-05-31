const express = require('express');
const OrderService = require('../services/OrderService');

const router = express.Router();

router.get('/', async (req, res) => {
  const orders = await OrderService.getAllOrders();
  res.json(orders);
});

router.get('/:id', async (req, res) => {
  const order = await OrderService.getOrder(req.params.id);
  res.json(order);
});

router.post('/', async (req, res) => {
  const order = await OrderService.createOrder(req.body);
  res.json(order);
});

module.exports = router;
