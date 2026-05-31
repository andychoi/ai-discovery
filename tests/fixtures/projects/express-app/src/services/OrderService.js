const Order = require('../models/Order');

class OrderService {
  async getAllOrders() {
    return Order.find();
  }

  async getOrder(id) {
    return Order.findById(id);
  }

  async createOrder(data) {
    const order = new Order(data);
    return order.save();
  }
}

module.exports = new OrderService();
