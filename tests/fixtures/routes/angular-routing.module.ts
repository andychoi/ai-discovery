import { CustomerListComponent } from './customer-list.component';
const routes: Routes = [
  { path: 'customers', component: CustomerListComponent, data: { title: 'Customers' } },
  { path: 'orders', loadChildren: () => import('./orders/orders.module') },
  { path: '', redirectTo: 'customers', pathMatch: 'full' },
];
