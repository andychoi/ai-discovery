<template>
  <form @submit.prevent="onSubmit">
    <input v-model="order.name" />
    <button type="submit">Save</button>
  </form>
</template>

<script setup>
import { reactive } from 'vue';
import { saveOrder } from '../api/orders';

const order = reactive({ name: '' });

function onSubmit() {
  saveOrder(order);
}
</script>
