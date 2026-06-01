const axios = require('axios');
const Stripe = require('stripe');
const stripe = new Stripe('sk_test');
const Redis = require('ioredis');
const redis = new Redis();

class PaymentService {
    async charge(order) {
        await axios.post('https://api.example.com/charge', order);
        await stripe.charges.create({ amount: order.total });
        await redis.set('last_order', order.id);
    }
}
module.exports = PaymentService;
