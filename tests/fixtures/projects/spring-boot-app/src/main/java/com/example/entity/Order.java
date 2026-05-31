package com.example.entity;

import javax.persistence.*;
import java.time.LocalDateTime;
import java.util.List;

@Entity
@Table(name = "orders")
public class Order {
    @Id
    @GeneratedValue
    private Long id;

    @ManyToOne
    private Customer customer;

    @OneToMany
    private List<OrderItem> items;

    private LocalDateTime createdAt;
    private String status;

    public Long getId() { return id; }
    public String getStatus() { return status; }
}
