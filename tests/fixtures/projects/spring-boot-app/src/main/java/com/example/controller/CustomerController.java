package com.example.controller;

import com.example.entity.Customer;
import com.example.service.CustomerService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/api/customers")
public class CustomerController {
    @Autowired
    private CustomerService customerService;

    @GetMapping
    public List<Customer> list() {
        return customerService.getCustomers();
    }

    @GetMapping("/{id}")
    public Customer get(@PathVariable Long id) {
        return customerService.getCustomer(id);
    }
}
