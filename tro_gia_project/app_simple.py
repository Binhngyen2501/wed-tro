from __future__ import annotations

import streamlit as st
from decimal import Decimal

# Payment Modal - MoMo & Cash
@st.dialog("Thanh toán hóa đơn", width="large")
def render_payment_dialog(payment):
    st.write(f"**{payment.description}**")
    st.write(f"Số tiền: **{payment.amount:,.0f} VND**")
    st.divider()
    
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button("📱 Thanh toán MoMo", key=f"momo_{payment.payment_id}", use_container_width=True, type="primary"):
            st.session_state.show_momo_qr = True
            st.session_state.show_cash_info = False
            st.rerun()
    
    with col2:
        if st.button("💵 Thanh toán tiền mặt", key=f"cash_{payment.payment_id}", use_container_width=True):
            st.session_state.show_momo_qr = False
            st.session_state.show_cash_info = True
            st.rerun()
    
    st.divider()
    
    # QR Code for MoMo
    if st.session_state.get("show_momo_qr", False):
        st.markdown("<center>", unsafe_allow_html=True)
        st.warning("📱 Quét mã QR để thanh toán qua MoMo")
        
        import qrcode
        qr_data = f"momo://pay?amount={payment.amount}&order=HD{payment.payment_id}"
        qr_img = qrcode.make(qr_data)
        
        c1, c2, c3 = st.columns([1, 2, 1])
        with c2:
            st.image(qr_img, use_container_width=True)
        
        st.info("⏳ Sau khi thanh toán, vui lòng nhấn 'Xác nhận'")
        
        if st.button("✅ Tôi đã thanh toán", key=f"confirm_{payment.payment_id}", type="primary", use_container_width=True):
            st.session_state[f"paid_{payment.payment_id}"] = True
            st.session_state.show_momo_qr = False
            st.success("🎉 Thanh toán thành công!")
            st.balloons()
        st.markdown("</center>", unsafe_allow_html=True)
    
    # Cash payment info
    if st.session_state.get("show_cash_info", False):
        st.info("""
        💵 **Thanh toán tiền mặt**
        
        Vui lòng liên hệ chủ trọ để thanh toán trực tiếp.
        
        📞 SĐT: 0909 000 000
        📍 Địa chỉ: 123 Đường ABC, Quận XYZ
        ⏰ Giờ làm việc: 8:00 - 20:00 hàng ngày
        """)
        
        if st.button("📞 Đã liên hệ chủ trọ", key=f"contact_{payment.payment_id}", use_container_width=True):
            st.session_state[f"paid_{payment.payment_id}"] = True
            st.session_state.show_cash_info = False
            st.success("✅ Đã ghi nhận! Vui lòng thanh toán với chủ trọ.")


# Main app
st.set_page_config(page_title="Tro Gia", layout="wide")
st.title("🏠 Tro Gia - Quản lý phòng trọ")

# Sidebar
with st.sidebar:
    st.markdown("### Chức năng")
    menu = st.radio("Menu", ["Trang chủ", "Hóa đơn của tôi"], label_visibility="collapsed")

if menu == "Trang chủ":
    st.info("👋 Chào mừng!")
    
elif menu == "Hóa đơn của tôi":
    st.header("💳 Hóa đơn của tôi")
    
    # Mock data
    class MockPayment:
        def __init__(self, pid, amount, desc):
            self.payment_id = pid
            self.amount = amount
            self.description = desc
    
    payments = [
        MockPayment(1, Decimal("2500000"), "Tiền thuê tháng 4/2025"),
        MockPayment(2, Decimal("1800000"), "Tiền thuê tháng 3/2025"),
    ]
    
    for p in payments:
        with st.container():
            col1, col2 = st.columns([3, 2])
            with col1:
                st.write(f"**{p.description}**")
                st.write(f"Số tiền: {p.amount:,.0f} VND")
            with col2:
                if st.button("💳 Thanh toán", key=f"pay_btn_{p.payment_id}", use_container_width=True, type="primary"):
                    render_payment_dialog(p)
        st.markdown("---")
