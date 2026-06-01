import { CustomerList } from './CustomerList';
const router = createBrowserRouter([
  { path: '/customers', element: <CustomerList />, handle: { title: 'Customers' } },
  { path: '/orders', lazy: () => import('./Orders') },
]);
