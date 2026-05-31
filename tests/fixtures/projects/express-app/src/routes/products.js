const express = require('express');
const ProductService = require('../services/ProductService');

const router = express.Router();

router.get('/', async (req, res) => {
  const products = await ProductService.getAllProducts();
  res.json(products);
});

router.get('/search', async (req, res) => {
  const query = req.query.q;
  const results = await ProductService.searchProducts(query);
  res.json(results);
});

router.get('/:id', async (req, res) => {
  const product = await ProductService.getProduct(req.params.id);
  res.json(product);
});

router.post('/', async (req, res) => {
  const product = await ProductService.createProduct(req.body);
  res.json(product);
});

module.exports = router;
