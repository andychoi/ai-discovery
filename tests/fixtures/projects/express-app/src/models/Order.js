const mongoose = require('mongoose');

const orderSchema = new mongoose.Schema({
  customerId: mongoose.Schema.Types.ObjectId,
  items: [Object],
  status: String,
  createdAt: { type: Date, default: Date.now }
});

module.exports = mongoose.model('Order', orderSchema);
