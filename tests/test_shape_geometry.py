import unittest
import shape_geometry as sg


class ShapeGeometryTests(unittest.TestCase):
    def test_same_route_with_small_redraw_is_equivalent(self):
        a = [[14.42000,50.08000],[14.42500,50.08100],[14.43000,50.08200]]
        # zhruba jednotky metrů posunu + jinak položený prostřední bod
        b = [[14.42003,50.08002],[14.42400,50.08082],[14.43002,50.08201]]
        self.assertTrue(sg.equivalent(a,b))

    def test_real_diversion_is_not_equivalent(self):
        a = [[14.42000,50.08000],[14.42500,50.08100],[14.43000,50.08200]]
        # prostředek je přibližně 100 m mimo původní trasu
        b = [[14.42000,50.08000],[14.42500,50.08200],[14.43000,50.08200]]
        self.assertFalse(sg.equivalent(a,b))

    def test_different_endpoint_is_not_equivalent(self):
        a = [[14.42000,50.08000],[14.43000,50.08200]]
        b = [[14.42000,50.08000],[14.43100,50.08200]]
        self.assertFalse(sg.equivalent(a,b))


if __name__ == "__main__": unittest.main()
