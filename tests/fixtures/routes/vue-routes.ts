import Layout from './Layout.vue';
const routes = [
  { path: '/', component: Layout, children: [
    { path: 'customers', name: 'customers', component: () => import('./pages/CustomerList.vue'),
      meta: { title: 'Customers', roles: ['sales'] } },
    { path: 'customers/:id', component: () => import('./pages/CustomerDetail.vue') },
  ]},
  { path: '/old', redirect: '/customers' },
  { path: '/:pathMatch(.*)*', component: () => import('./pages/NotFound.vue') },
];
export default routes;
