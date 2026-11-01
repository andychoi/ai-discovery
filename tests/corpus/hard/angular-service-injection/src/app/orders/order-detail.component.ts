import { OrderService } from './order.service';

export class OrderDetailComponent {
  constructor(private readonly orders: OrderService) {}

  open(id: number): number {
    return this.orders.load(id);
  }
}
